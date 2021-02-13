# obc.py - optimization based control module
#
# RMM, 11 Feb 2021
#

"""The "mod:`~control.obc` module provides support for optimization-based
controllers for nonlinear systems with state and input constraints.

"""

import numpy as np
import scipy as sp
import scipy.optimize as opt
import control as ct
import warnings

from .timeresp import _process_time_response

#
# OptimalControlProblem class
#
# The OptimalControlProblem class holds all of the information required to
# specify and optimal control problem: the system dynamics, cost function,
# and constraints.  As much as possible, the information used to specify an
# optimal control problem matches the notation and terminology of the SciPy
# `optimize.minimize` module, with the hope that this makes it easier to
# remember how to describe a problem.
#
# The approach that we use here is to set up an optimization over the
# inputs at each point in time, using the integral and terminal costs as
# well as the trajectory and terminal constraints.  The main function of
# this class is to create an optimization problem that can be solved using
# scipy.optimize.minimize().
#
# The `cost_function` method takes the information stored here and computes
# the cost of the trajectory generated by the proposed input.  It does this
# by calling a user-defined function for the integral_cost given the
# current states and inputs at each point along the trajetory and then
# adding the value of a user-defined terminal cost at the final pint in the
# trajectory.
#
# The `constraint_function` method evaluates the constraint functions along
# the trajectory generated by the proposed input.  As in the case of the
# cost function, the constraints are evaluated at the state and input along
# each point on the trjectory.  This information is compared against the
# constraint upper and lower bounds.  The constraint function is processed
# in the class initializer, so that it only needs to be computed once.
#
class OptimalControlProblem():
    """The :class:`OptimalControlProblem` class is a front end for computing an
    optimal control input for a nonilinear system with a user-defined cost
    function and state and input constraints.

    """
    def __init__(
            self, sys, time, integral_cost, trajectory_constraints=[],
            terminal_cost=None, terminal_constraints=[]):
        # Save the basic information for use later
        self.system = sys
        self.time_vector = time
        self.integral_cost = integral_cost
        self.trajectory_constraints = trajectory_constraints
        self.terminal_cost = terminal_cost
        self.terminal_constraints = terminal_constraints

        #
        # Compute and store constraints
        #
        # While the constraints are evaluated during the execution of the
        # SciPy optimization method itself, we go ahead and pre-compute the
        # `scipy.optimize.NonlinearConstraint` function that will be passed to
        # the optimizer on initialization, since it doesn't change.  This is
        # mainly a matter of computing the lower and upper bound vectors,
        # which we need to "stack" to account for the evaluation at each
        # trajectory time point plus any terminal constraints (in a way that
        # is consistent with the `constraint_function` that is used at
        # evaluation time.
        #
        constraint_lb, constraint_ub = [], []

        # Go through each time point and stack the bounds
        for time in self.time_vector:
            for constraint in self.trajectory_constraints:
                type, fun, lb, ub = constraint
                constraint_lb.append(lb)
                constraint_ub.append(ub)

        # Add on the terminal constraints
        for constraint in self.terminal_constraints:
            type, fun, lb, ub = constraint
            constraint_lb.append(lb)
            constraint_ub.append(ub)

        # Turn constraint vectors into 1D arrays
        self.constraint_lb = np.hstack(constraint_lb)
        self.constraint_ub = np.hstack(constraint_ub)

        # Create the new constraint
        self.constraints = sp.optimize.NonlinearConstraint(
            self.constraint_function, self.constraint_lb, self.constraint_ub)

        #
        # Initial guess
        #
        # We store an initial guess (zero input) in case it is not specified
        # later.
        #
        # TODO: add the ability to overwride this when calling the optimizer.
        #
        self.initial_guess = np.zeros(
            self.system.ninputs * self.time_vector.size)

    #
    # Cost function
    #
    # Given the input U = [u[0], ... u[N]], we need to compute the cost of
    # the trajectory generated by that input.  This means we have to
    # simulate the system to get the state trajectory X = [x[0], ...,
    # x[N]] and then compute the cost at each point:
    #
    #   Cost = sum_k integral_cost(x[k], u[k]) + terminal_cost(x[N], u[N])
    #
    # The initial state is for generating the simulation is store in the class
    # parameter `x` prior to calling the optimization algorithm.
    #
    def cost_function(self, inputs):
        # Retrieve the initial state and reshape the input vector
        x = self.x
        inputs = inputs.reshape(
            (self.system.ninputs, self.time_vector.size))
        
        # Simulate the system to get the state
        _, _, states = ct.input_output_response(
            self.system, self.time_vector, inputs, x, return_x=True)
        
        # Trajectory cost
        # TODO: vectorize
        cost = 0
        for i, time in enumerate(self.time_vector):
            cost += self.integral_cost(states[:,i], inputs[:,i]) 
            
        # Terminal cost
        if self.terminal_cost is not None:
            cost += self.terminal_cost(states[:,-1], inputs[:,-1])
            
        # Return the total cost for this input sequence
        return cost

    #
    # Constraints
    #
    # We are given the constraints along the trajectory and the terminal
    # constraints, which each take inputs [x, u] and evaluate the
    # constraint.  How we handle these depends on the type of constraint:
    #
    # * For linear constraints (LinearConstraint), a combined vector of the
    #   state and input is multiplied by the polytope A matrix for
    #   comparison against the upper and lower bounds.
    #
    # * For nonlinear constraints (NonlinearConstraint), a user-specific
    #   constraint function having the form
    #
    #      constraint_fun(x, u)
    #
    #   is called at each point along the trajectory and compared against the
    #   upper and lower bounds.
    #
    # In both cases, the constraint is specified at a single point, but we
    # extend this to apply to each point in the trajectory.  This means
    # that for N time points with m trajectory constraints and p terminal
    # constraints we need to compute N*m + p constraints, each of which
    # holds at a specific point in time, and implements the original
    # constraint.
    #
    # To do this, we basically create a function that simulates the system
    # dynamics and returns a vector of values corresponding to the value of
    # the function at each time.  The class initialization methods takes
    # care of replicating the upper and lower bounds for each point in time
    # so that the SciPy optimization algorithm can do the proper
    # evaluation.
    #
    # In addition, since SciPy's optimization function does not allow us to
    # pass arguments to the constraint function, we have to store the initial
    # state prior to optimization and retrieve it here.
    #
    def constraint_function(self, inputs):
        # Retrieve the initial state and reshape the input vector
        x = self.x
        inputs = inputs.reshape(
            (self.system.ninputs, self.time_vector.size))
            
        # Simulate the system to get the state
        _, _, states = ct.input_output_response(
            self.system, self.time_vector, inputs, x, return_x=True)

        # Evaluate the constraint function along the trajectory
        value = []
        for i, time in enumerate(self.time_vector):
            for constraint in self.trajectory_constraints:
                type, fun, lb, ub = constraint
                if type == opt.LinearConstraint:
                    # `fun` is the A matrix associated with the polytope...
                    value.append(
                        np.dot(fun, np.hstack([states[:,i], inputs[:,i]])))
                elif type == opt.NonlinearConstraint:
                    value.append(
                        fun(np.hstack([states[:,i], inputs[:,i]])))
                else:
                    raise TypeError("unknown constraint type %s" %
                                    constraint[0])

        # Evaluate the terminal constraint functions
        for constraint in self.terminal_constraints:
            type, fun, lb, ub = constraint
            if type == opt.LinearConstraint:
                value.append(
                    np.dot(fun, np.hstack([states[:,i], inputs[:,i]])))
            elif type == opt.NonlinearConstraint:
                value.append(
                    fun(np.hstack([states[:,i], inputs[:,i]])))
            else:
                raise TypeError("unknown constraint type %s" %
                                constraint[0])

        # Return the value of the constraint function
        return np.hstack(value)

    # Allow optctrl(x) as a replacement for optctrl.mpc(x)
    def __call__(self, x, squeeze=None):
        """Compute the optimal input at state x"""
        return self.mpc(x, squeeze=squeeze)

    # Compute the current input to apply from the current state (MPC style)
    def mpc(self, x, squeeze=None):
        """Compute the optimal input at state x"""
        _, inputs = self.compute_trajectory(x, squeeze=squeeze)
        return None if inputs is None else inputs.transpose()[0]

    # Compute the optimal trajectory from the current state
    def compute_trajectory(
            self, x, squeeze=None, transpose=None, return_x=None):
        """Compute the optimal input at state x"""
        # Store the initial state (for use in constraint_function)
        self.x = x
        
        # Call ScipPy optimizer
        res = sp.optimize.minimize(
            self.cost_function, self.initial_guess,
            constraints=self.constraints)

        # See if we got an answer
        if not res.success:
            warnings.warn(res.message)
            return None

        # Reshape the input vector
        inputs = res.x.reshape(
            (self.system.ninputs, self.time_vector.size))

        if return_x:
            # Simulate the system if we need the state back
            _, _, states = ct.input_output_response(
                self.system, self.time_vector, inputs, x, return_x=True)
        else:
            states=None
            
        return _process_time_response(
            self.system, self.time_vector, inputs, states,
            transpose=transpose, return_x=return_x, squeeze=squeeze)


#
# Create a polytope constraint on the system state
#
# As in the cost function evaluation, the main "trick" in creating a constrain
# on the state or input is to properly evaluate the constraint on the stacked
# state and input vector at the current time point.  The constraint itself
# will be called at each poing along the trajectory (or the endpoint) via the
# constrain_function() method.
#
# Note that these functions to not actually evaluate the constraint, they
# simply return the information required to do so.  We use the SciPy
# optimization methods LinearConstraint and NonlinearConstraint as "types" to
# keep things consistent with the terminology in scipy.optimize.
#
def state_poly_constraint(sys, polytope):
    """Create state constraint from polytope"""
    # TODO: make sure the system and constraints are compatible

    # Return a linear constraint object based on the polynomial
    return (opt.LinearConstraint,
            np.hstack(
                [polytope.A, np.zeros((polytope.A.shape[0], sys.ninputs))]),
            np.full(polytope.A.shape[0], -np.inf), polytope.b)

# Create a constraint polytope on the system input
def input_poly_constraint(sys, polytope):
    """Create input constraint from polytope"""
    # TODO: make sure the system and constraints are compatible

    # Return a linear constraint object based on the polynomial
    return (opt.LinearConstraint,
            np.hstack(
                [np.zeros((polytope.A.shape[0], sys.nstates)), polytope.A]),
            np.full(polytope.A.shape[0], -np.inf), polytope.b)


#
# Create a constraint polytope on the system output
#
# Unlike the state and input constraints, for the output constraint we need to
# do a function evaluation before applying the constraints.
#
# TODO: for the special case of an LTI system, we can avoid the extra function
# call by multiplying the state by the C matrix for the system and then
# imposing a linear constraint:
#
#     np.hstack(
#         [polytope.A @ sys.C, np.zeros((polytope.A.shape[0], sys.ninputs))])
#
def output_poly_constraint(sys, polytope):
    """Create output constraint from polytope"""
    # TODO: make sure the system and constraints are compatible

    #
    # Function to create the output
    def _evaluate_output_constraint(x):
        # Separate the constraint into states and inputs
        states = x[:sys.nstates]
        inputs = x[sys.nstates:]
        outputs = sys._out(0, states, inputs)
        return polytope.A @ outputs

    # Return a nonlinear constraint object based on the polynomial
    return (opt.NonlinearConstraint,
            _evaluate_output_constraint,
            np.full(polytope.A.shape[0], -np.inf), polytope.b)


#
# Quadratic cost function
#
# Since a quadratic function is common as a cost function, we provide a
# function that will take a Q and R matrix and return a callable that
# evaluates to associted quadratic cost.  This is compatible with the way that
# the `cost_function` evaluates the cost at each point in the trajectory.
#
def quadratic_cost(sys, Q, R):
    """Create quadratic cost function"""
    Q = np.atleast_2d(Q)
    R = np.atleast_2d(R)
    return lambda x, u: x @ Q @ x + u @ R @ u
