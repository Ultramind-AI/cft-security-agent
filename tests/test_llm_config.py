from app.config import Settings


def test_settings_load_provider_keys_from_plain_dotenv_names(tmp_path, monkeypatch) -> None:
    # Для теста используем только его .env.
    monkeypatch.delenv("CFT_AGENT_MODE", raising=False)
    monkeypatch.delenv("NSU_OPENWEBUI_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        """NSU_OPENWEBUI_KEY=test-nsu
GROQ_API_KEY=test-groq
MISTRAL_API_KEY=test-mistral
CLOUDFLARE_API_TOKEN=test-cf
CLOUDFLARE_ACCOUNT_ID=test-account
CFT_AGENT_MODE=llm
""",
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)
    credentials = settings.llm_provider_credentials()

    assert settings.agent_mode == "llm"
    assert credentials["NSU_OPENWEBUI_KEY"] == "test-nsu"
    assert credentials["GROQ_API_KEY"] == "test-groq"
    assert credentials["MISTRAL_API_KEY"] == "test-mistral"
    assert credentials["CLOUDFLARE_API_TOKEN"] == "test-cf"
    assert credentials["CLOUDFLARE_ACCOUNT_ID"] == "test-account"


def test_provider_secrets_are_masked_in_settings_repr(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("GROQ_API_KEY=super-secret-test-value\n", encoding="utf-8")

    settings = Settings(_env_file=env_file)

    assert "super-secret-test-value" not in repr(settings)


def test_nsu_is_the_only_default_route_family(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("CFT_LLM_ALLOW_EXTERNAL_FALLBACKS=false\n", encoding="utf-8")

    settings = Settings(_env_file=env_file)

    assert settings.llm_allow_external_fallbacks is False
