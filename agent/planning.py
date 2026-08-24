from __future__ import annotations

from schemas.plan import DynamicPlan, PlanValidationResult
from schemas.state import AgentState


class DynamicPlanValidator:
    """Validate plan scope against trusted runtime/application state.

    This is not a replacement for PolicyValidator. It only proves that a plan does
    not reference a target, service, endpoint or sandbox session outside the state
    that was supplied to the agent.
    """

    def validate(self, plan: DynamicPlan, state: AgentState) -> PlanValidationResult:
        rules: list[str] = []
        profile = state.get("target_profile")
        if profile is None:
            return self._deny(plan, "TargetProfile is required for DynamicPlan", rules)

        if plan.target != profile.id:
            return self._deny(plan, "DynamicPlan target is outside current TargetProfile", rules)
        rules.append("target_matches_profile")

        if plan.environment != profile.environment:
            return self._deny(
                plan,
                "DynamicPlan environment is outside current TargetProfile",
                rules,
            )
        rules.append("environment_matches_profile")

        hypothesis = state.get("hypothesis")
        if hypothesis is None or plan.hypothesis_id != hypothesis.id:
            return self._deny(plan, "DynamicPlan hypothesis does not match current state", rules)
        rules.append("hypothesis_matches_state")

        current_iteration = int(state.get("iteration_count", 0))
        max_iterations = int(state.get("max_iterations", 2))
        remaining = max_iterations - current_iteration
        if remaining < 1:
            return self._deny(plan, "No iteration budget remains for DynamicPlan", rules)
        if plan.max_steps > remaining or len(plan.steps) > remaining:
            return self._deny(plan, "DynamicPlan exceeds remaining iteration budget", rules)
        rules.append("within_iteration_budget")

        runtime_services = state.get("runtime_services")
        runtime_scoped = any(
            step.action.service is not None or step.action.endpoint is not None
            for step in plan.steps
        )

        if plan.sandbox_session_id is not None:
            if runtime_services is None:
                return self._deny(
                    plan,
                    "DynamicPlan references a sandbox session without RuntimeServiceMap",
                    rules,
                )
            if plan.sandbox_session_id != runtime_services.session_id:
                return self._deny(
                    plan,
                    "DynamicPlan sandbox session is not the current RuntimeServiceMap session",
                    rules,
                )
            rules.append("sandbox_session_matches_runtime_map")
        elif runtime_scoped:
            return self._deny(
                plan,
                "Runtime-scoped DynamicPlan requires the current sandbox session",
                rules,
            )

        for expected_index, step in enumerate(plan.steps, start=1):
            action = step.action
            expected_iteration = current_iteration + expected_index
            if action.iteration != expected_iteration:
                return self._deny(
                    plan,
                    "DynamicPlan action iteration does not match plan order",
                    rules,
                )
            if action.target != profile.id or action.environment != profile.environment:
                return self._deny(
                    plan,
                    "DynamicPlan action escapes current target scope",
                    rules,
                )

            if action.endpoint is not None and action.service is None:
                return self._deny(plan, "Endpoint reference requires a service", rules)

            if action.service is not None:
                if action.service not in profile.services:
                    return self._deny(
                        plan,
                        f"DynamicPlan references unknown target service '{action.service}'",
                        rules,
                    )
                if runtime_services is None:
                    return self._deny(
                        plan,
                        "Runtime service reference requires RuntimeServiceMap",
                        rules,
                    )
                runtime_service = runtime_services.services.get(action.service)
                if runtime_service is None or not runtime_service.ready:
                    return self._deny(
                        plan,
                        f"DynamicPlan service '{action.service}' is not ready in RuntimeServiceMap",
                        rules,
                    )
                if action.endpoint is not None and action.endpoint not in runtime_service.allowed_endpoints:
                    return self._deny(
                        plan,
                        (
                            f"DynamicPlan endpoint '{action.endpoint}' is not allowed for "
                            f"service '{action.service}'"
                        ),
                        rules,
                    )

        rules.append("all_actions_within_runtime_scope")
        return PlanValidationResult(
            approved=True,
            plan_id=plan.id,
            reason="DynamicPlan is within current target/runtime scope",
            rules=rules,
        )

    @staticmethod
    def _deny(
        plan: DynamicPlan,
        reason: str,
        rules: list[str],
    ) -> PlanValidationResult:
        return PlanValidationResult(
            approved=False,
            plan_id=plan.id,
            reason=reason,
            rules=[*rules, "dynamic_plan_scope_denied"],
        )
