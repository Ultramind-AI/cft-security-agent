from schemas.action import ActionProposal
from validator.validator import PolicyValidator
from executor.executor import SafeExecutor

def main() -> None:
    proposal = ActionProposal(
        id="demo-action-001",
        tool="safe_noop",
        target="sberlab-local",
        parameters={"message": "contract-check"},
        purpose="Validate starter integration",
        expected_evidence="A structured successful no-op result",
    )
    validator = PolicyValidator.from_yaml("policies/default.yaml")
    validation = validator.validate(proposal)
    result = SafeExecutor().execute(proposal, validation)
    print("Validation:", validation.model_dump())
    print("Execution:", result.model_dump())

if __name__ == "__main__":
    main()
