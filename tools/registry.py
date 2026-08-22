class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, object] = {}
        self._endpoints: dict[str, str] = {}

    def register(self, name: str, tool: object, *, endpoint: str | None = None) -> None:
        if name in self._tools:
            raise ValueError(f"Tool already registered: {name}")
        self._tools[name] = tool
        if endpoint is not None:
            self._endpoints[name] = endpoint

    def get(self, name: str) -> object:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"Unknown tool: {name}") from exc

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def endpoint(self, name: str) -> str | None:
        self.get(name)
        return self._endpoints.get(name)
