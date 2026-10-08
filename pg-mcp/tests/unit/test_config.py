"""Unit tests for configuration management.

Tests for all configuration classes to ensure proper validation,
defaults, and environment variable parsing.
"""

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from pg_mcp.config.settings import (
    CacheConfig,
    DatabaseConfig,
    ObservabilityConfig,
    OpenAIConfig,
    ResilienceConfig,
    SecurityConfig,
    Settings,
    ValidationConfig,
    get_settings,
    reset_settings,
)


class TestDatabaseConfig:
    """Tests for DatabaseConfig."""

    def test_default_values(self) -> None:
        """Test default configuration values.

        _env_file=None 隔离本机真实 .env, 否则断言的"默认值"会被它覆盖。
        """
        config = DatabaseConfig(_env_file=None)
        assert config.host == "localhost"
        assert config.port == 5432
        assert config.name == "postgres"
        assert config.user == "postgres"
        assert config.min_pool_size == 5
        assert config.max_pool_size == 20

    def test_custom_values(self) -> None:
        """Test custom configuration values."""
        config = DatabaseConfig(
            host="db.example.com",
            port=5433,
            name="mydb",
            user="myuser",
            password="secret",
        )
        assert config.host == "db.example.com"
        assert config.port == 5433
        assert config.name == "mydb"
        assert config.user == "myuser"
        assert config.password.get_secret_value() == "secret"

    def test_dsn_generation(self) -> None:
        """Test DSN string generation."""
        config = DatabaseConfig(
            host="localhost",
            port=5432,
            name="testdb",
            user="testuser",
            password="testpass",
        )
        dsn = config.dsn
        assert dsn == "postgresql://testuser:testpass@localhost:5432/testdb"

    def test_safe_dsn_masks_password(self) -> None:
        """Test safe DSN masks password."""
        config = DatabaseConfig(
            host="localhost",
            port=5432,
            name="testdb",
            user="testuser",
            password="secret123",
        )
        safe_dsn = config.safe_dsn
        assert "secret123" not in safe_dsn
        assert "***" in safe_dsn
        assert "testuser" in safe_dsn

    def test_password_is_not_exposed_in_repr(self) -> None:
        """Test the database password never appears in a model repr.

        回归背景: password 曾是裸 str 而非 SecretStr, 导致 print(model) 以及
        pytest 断言失败的 diff 都会明文打印真实数据库密码 (2026-10-08 两次真实泄漏)。
        断言必须先投影成标量, 绝不能直接比较 model 列表。
        """
        config = DatabaseConfig(
            name="testdb", user="testuser", password="s3cr3t-value"
        )

        assert "s3cr3t-value" not in repr(config)
        assert "s3cr3t-value" not in str(config)
        assert config.password.get_secret_value() == "s3cr3t-value"

    def test_invalid_port(self) -> None:
        """Test invalid port number is rejected."""
        with pytest.raises(ValidationError):
            DatabaseConfig(port=0)

        with pytest.raises(ValidationError):
            DatabaseConfig(port=99999)

    def test_invalid_pool_size(self) -> None:
        """Test invalid pool size is rejected."""
        with pytest.raises(ValidationError):
            DatabaseConfig(min_pool_size=0)

        with pytest.raises(ValidationError):
            DatabaseConfig(max_pool_size=101)


class TestOpenAIConfig:
    """Tests for OpenAIConfig."""

    def test_default_values(self) -> None:
        """Test default configuration values.

        _env_file=None 隔离本机真实 .env, 同上。
        """
        config = OpenAIConfig(_env_file=None, api_key="sk-test123")
        assert config.model == "gpt-4o-mini"
        assert config.max_tokens == 2000
        assert config.temperature == 0.0
        assert config.timeout == 30.0

    def test_custom_values(self) -> None:
        """Test custom configuration values."""
        config = OpenAIConfig(
            api_key="sk-custom",
            model="gpt-4",
            max_tokens=4000,
            temperature=0.7,
            timeout=60.0,
        )
        assert config.model == "gpt-4"
        assert config.max_tokens == 4000
        assert config.temperature == 0.7
        assert config.timeout == 60.0

    def test_empty_api_key_rejected(self) -> None:
        """Test empty API key is rejected."""
        with pytest.raises(ValidationError, match="must not be empty"):
            OpenAIConfig(api_key="")

    def test_whitespace_api_key_rejected(self) -> None:
        """Test whitespace-only API key is rejected."""
        with pytest.raises(ValidationError, match="must not be empty"):
            OpenAIConfig(api_key="   ")

    def test_invalid_api_key_format(self) -> None:
        """Test API key must start with sk-."""
        with pytest.raises(ValidationError, match="must start with 'sk-'"):
            OpenAIConfig(api_key="invalid-key")

    def test_invalid_max_tokens(self) -> None:
        """Test invalid max_tokens is rejected."""
        with pytest.raises(ValidationError):
            OpenAIConfig(api_key="sk-test", max_tokens=50)

        with pytest.raises(ValidationError):
            OpenAIConfig(api_key="sk-test", max_tokens=5000)

    def test_invalid_temperature(self) -> None:
        """Test invalid temperature is rejected."""
        with pytest.raises(ValidationError):
            OpenAIConfig(api_key="sk-test", temperature=-0.1)

        with pytest.raises(ValidationError):
            OpenAIConfig(api_key="sk-test", temperature=2.1)


class TestSecurityConfig:
    """Tests for SecurityConfig."""

    def test_default_values(self) -> None:
        """Test default configuration values."""
        config = SecurityConfig(_env_file=None)
        assert config.max_rows == 10000
        assert config.max_execution_time == 30.0
        assert "pg_sleep" in config.blocked_functions
        assert "pg_read_file" in config.blocked_functions

    def test_custom_blocked_functions(self) -> None:
        """Test custom blocked functions."""
        config = SecurityConfig(
            blocked_functions=["func1", "func2"],
        )
        assert config.blocked_functions == ["func1", "func2"]

    def test_parse_blocked_functions_from_string(self) -> None:
        """Test parsing blocked functions from comma-separated string."""
        config = SecurityConfig(
            blocked_functions="func1, func2, func3",  # type: ignore
        )
        assert "func1" in config.blocked_functions
        assert "func2" in config.blocked_functions
        assert "func3" in config.blocked_functions

    def test_blocked_tables_default_empty(self) -> None:
        """Test blocked_tables defaults to an empty list.

        用 _env_file=None 隔离真实 .env, 否则本机 .env 的配置会让断言失效。
        """
        config = SecurityConfig(_env_file=None)
        assert config.blocked_tables == []

    def test_blocked_columns_default_empty(self) -> None:
        """Test blocked_columns defaults to an empty list.

        用 _env_file=None 隔离真实 .env, 同上。
        """
        config = SecurityConfig(_env_file=None)
        assert config.blocked_columns == []

    def test_allow_explain_defaults_false(self) -> None:
        """Test EXPLAIN is disabled unless explicitly enabled."""
        config = SecurityConfig()
        assert config.allow_explain is False

    def test_custom_blocked_tables_and_columns(self) -> None:
        """Test custom blocked tables and columns."""
        config = SecurityConfig(
            blocked_tables=["users", "api_keys"],
            blocked_columns=["password", "ssn"],
            allow_explain=True,
        )
        assert config.blocked_tables == ["users", "api_keys"]
        assert config.blocked_columns == ["password", "ssn"]
        assert config.allow_explain is True

    def test_parse_blocked_tables_from_string(self) -> None:
        """Test parsing blocked tables and columns from comma-separated strings."""
        config = SecurityConfig(
            blocked_tables="users, api_keys",  # type: ignore
            blocked_columns="password, ssn",  # type: ignore
        )
        assert config.blocked_tables == ["users", "api_keys"]
        assert config.blocked_columns == ["password", "ssn"]

    def test_reads_security_settings_from_env_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test SECURITY_* settings are read from the .env file.

        回归背景: SecurityConfig 只有 env_prefix 而无 env_file, 导致 .env 里的
        SECURITY_BLOCKED_TABLES / SECURITY_BLOCKED_COLUMNS 被静默忽略,
        运行时表现为黑名单恒为空集、敏感对象零保护。
        """
        monkeypatch.chdir(tmp_path)
        for key in (
            "SECURITY_BLOCKED_TABLES",
            "SECURITY_BLOCKED_COLUMNS",
            "SECURITY_MAX_ROWS",
        ):
            monkeypatch.delenv(key, raising=False)

        (tmp_path / ".env").write_text(
            "SECURITY_BLOCKED_TABLES=t_j_dict,victim_phone_log\n"
            "SECURITY_BLOCKED_COLUMNS=phone\n"
            "SECURITY_MAX_ROWS=500\n",
            encoding="utf-8",
        )

        config = SecurityConfig()

        assert config.blocked_tables == ["t_j_dict", "victim_phone_log"]
        assert config.blocked_columns == ["phone"]
        assert config.max_rows == 500

    def test_allow_write_operations_field_removed(self) -> None:
        """Test the inert allow_write_operations field is gone.

        该字段从未被任何代码读取, 而写操作本来就由 SQLValidator 的
        FORBIDDEN_STATEMENT_TYPES 无条件禁止; 留着只会让人以为能放开。
        """
        assert "allow_write_operations" not in SecurityConfig.model_fields

    def test_invalid_max_rows(self) -> None:
        """Test invalid max_rows is rejected."""
        with pytest.raises(ValidationError):
            SecurityConfig(max_rows=0)

        with pytest.raises(ValidationError):
            SecurityConfig(max_rows=100001)


class TestValidationConfig:
    """Tests for ValidationConfig."""

    def test_default_values(self) -> None:
        """Test default configuration values."""
        config = ValidationConfig()
        assert config.max_question_length == 10000
        assert config.confidence_threshold == 70

    def test_custom_values(self) -> None:
        """Test custom configuration values."""
        config = ValidationConfig(
            max_question_length=5000,
            confidence_threshold=80,
        )
        assert config.max_question_length == 5000
        assert config.confidence_threshold == 80

    def test_min_confidence_score_field_removed(self) -> None:
        """Test the duplicate min_confidence_score field is gone.

        它与 confidence_threshold 同义且同默认值, 却从未被读取;
        result_validator 用的是 confidence_threshold。
        """
        assert "min_confidence_score" not in ValidationConfig.model_fields

    def test_invalid_confidence_threshold(self) -> None:
        """Test invalid confidence threshold is rejected."""
        with pytest.raises(ValidationError):
            ValidationConfig(confidence_threshold=-1)

        with pytest.raises(ValidationError):
            ValidationConfig(confidence_threshold=101)


class TestCacheConfig:
    """Tests for CacheConfig."""

    def test_default_values(self) -> None:
        """Test default configuration values."""
        config = CacheConfig()
        assert config.schema_ttl == 3600
        assert config.max_size == 100
        assert config.enabled is True

    def test_custom_values(self) -> None:
        """Test custom configuration values."""
        config = CacheConfig(
            schema_ttl=7200,
            max_size=200,
            enabled=False,
        )
        assert config.schema_ttl == 7200
        assert config.max_size == 200
        assert config.enabled is False

    def test_invalid_ttl(self) -> None:
        """Test invalid TTL is rejected."""
        with pytest.raises(ValidationError):
            CacheConfig(schema_ttl=30)

        with pytest.raises(ValidationError):
            CacheConfig(schema_ttl=90000)


class TestResilienceConfig:
    """Tests for ResilienceConfig."""

    def test_default_values(self) -> None:
        """Test default configuration values."""
        config = ResilienceConfig()
        assert config.max_retries == 3
        assert config.retry_delay == 1.0
        assert config.backoff_factor == 2.0
        assert config.circuit_breaker_threshold == 5
        assert config.circuit_breaker_timeout == 60.0

    def test_custom_values(self) -> None:
        """Test custom configuration values."""
        config = ResilienceConfig(
            max_retries=5,
            retry_delay=2.0,
            backoff_factor=3.0,
        )
        assert config.max_retries == 5
        assert config.retry_delay == 2.0
        assert config.backoff_factor == 3.0

    def test_invalid_values(self) -> None:
        """Test invalid values are rejected."""
        with pytest.raises(ValidationError):
            ResilienceConfig(max_retries=-1)

        with pytest.raises(ValidationError):
            ResilienceConfig(backoff_factor=0.5)

    def test_rate_limit_defaults(self) -> None:
        """Test concurrency limits are configurable rather than hardcoded.

        回归背景: server.py 曾把 10/5 写死在构造函数里并注明
        "Can be made configurable"。
        """
        config = ResilienceConfig()
        assert config.query_limit == 10
        assert config.llm_limit == 5

    def test_custom_rate_limits(self) -> None:
        """Test custom concurrency limits."""
        config = ResilienceConfig(query_limit=3, llm_limit=2)
        assert config.query_limit == 3
        assert config.llm_limit == 2

    def test_invalid_rate_limits(self) -> None:
        """Test non-positive concurrency limits are rejected."""
        with pytest.raises(ValidationError):
            ResilienceConfig(query_limit=0)

        with pytest.raises(ValidationError):
            ResilienceConfig(llm_limit=0)


class TestObservabilityConfig:
    """Tests for ObservabilityConfig."""

    def test_default_values(self) -> None:
        """Test default configuration values."""
        config = ObservabilityConfig()
        # metrics_enabled 在测试环境可能被禁用以避免启动 HTTP 服务器
        # 生产环境应该通过环境变量显式设置
        assert config.metrics_port == 9090
        assert config.log_level == "INFO"
        assert config.log_format == "json"

    def test_custom_values(self) -> None:
        """Test custom configuration values."""
        config = ObservabilityConfig(
            metrics_enabled=False,
            metrics_port=8080,
            log_level="DEBUG",
            log_format="text",
        )
        assert config.metrics_enabled is False
        assert config.metrics_port == 8080
        assert config.log_level == "DEBUG"
        assert config.log_format == "text"

    def test_invalid_log_level(self) -> None:
        """Test invalid log level is rejected."""
        with pytest.raises(ValidationError):
            ObservabilityConfig(log_level="INVALID")  # type: ignore

    def test_invalid_log_format(self) -> None:
        """Test invalid log format is rejected."""
        with pytest.raises(ValidationError):
            ObservabilityConfig(log_format="xml")  # type: ignore


class TestSettings:
    """Tests for main Settings class."""

    def test_default_settings(self) -> None:
        """Test default settings initialization."""
        settings = Settings(openai=OpenAIConfig(api_key="sk-test"))
        assert settings.environment == "development"
        assert settings.database is not None
        assert settings.openai is not None
        assert settings.security is not None
        assert settings.validation is not None
        assert settings.cache is not None
        assert settings.resilience is not None
        assert settings.observability is not None

    def test_is_production(self) -> None:
        """Test production environment check."""
        settings = Settings(
            environment="production",
            openai=OpenAIConfig(api_key="sk-test"),
        )
        assert settings.is_production
        assert not settings.is_development

    def test_is_development(self) -> None:
        """Test development environment check."""
        settings = Settings(
            environment="development",
            openai=OpenAIConfig(api_key="sk-test"),
        )
        assert settings.is_development
        assert not settings.is_production

    def test_nested_config_override(self) -> None:
        """Test overriding nested configurations."""
        settings = Settings(
            openai=OpenAIConfig(api_key="sk-test"),
            database=DatabaseConfig(
                host="custom.host",
                port=5433,
            ),
            security=SecurityConfig(
                max_rows=1234,
            ),
        )
        assert settings.database.host == "custom.host"
        assert settings.database.port == 5433
        assert settings.security.max_rows == 1234


class TestMultiDatabaseSettings:
    """Tests for multi-database configuration resolution."""

    def test_effective_databases_falls_back_to_single_database(self) -> None:
        """Test single-database config stays working (backward compatibility)."""
        settings = Settings(
            _env_file=None,  # 隔离本机真实 .env, 否则它会提供 databases
            openai=OpenAIConfig(api_key="sk-test"),
            database=DatabaseConfig(name="only_db", host="legacy.host"),
        )
        # 投影成标量再断言: 直接比较 model 列表时 pytest 的失败 diff 会打印明文密码
        assert [db.name for db in settings.databases] == []
        assert [db.name for db in settings.effective_databases] == ["only_db"]
        assert settings.effective_databases[0].host == "legacy.host"

    def test_effective_databases_uses_configured_list(self) -> None:
        """Test the configured list wins when present."""
        settings = Settings(
            openai=OpenAIConfig(api_key="sk-test"),
            database=DatabaseConfig(name="ignored"),
            databases=[
                DatabaseConfig(name="blog"),
                DatabaseConfig(name="shop", host="shop.host"),
            ],
        )
        names = [db.name for db in settings.effective_databases]
        assert names == ["blog", "shop"]
        assert settings.effective_databases[1].host == "shop.host"

    def test_databases_accepts_nested_dicts(self) -> None:
        """Test databases parses from plain dicts (env/JSON style)."""
        settings = Settings(
            openai=OpenAIConfig(api_key="sk-test"),
            databases=[
                {"name": "blog", "host": "blog.host", "port": 5433},
                {"name": "shop", "user": "reader"},
            ],
        )
        assert settings.databases[0].name == "blog"
        assert settings.databases[0].port == 5433
        assert settings.databases[1].user == "reader"

    def test_duplicate_database_names_rejected(self) -> None:
        """Test duplicate database names are rejected.

        Pool/executor 字典都以 name 为键,重名会静默互相覆盖。
        """
        with pytest.raises(ValidationError, match=r"[Dd]uplicate"):
            Settings(
                openai=OpenAIConfig(api_key="sk-test"),
                databases=[
                    DatabaseConfig(name="blog"),
                    DatabaseConfig(name="blog"),
                ],
            )


class TestSettingsGlobalInstance:
    """Tests for global settings instance management."""

    def teardown_method(self) -> None:
        """Clean up after each test."""
        reset_settings()
        # Clean up environment variables
        for key in list(os.environ.keys()):
            if key.startswith(("DATABASE_", "OPENAI_", "SECURITY_")):
                del os.environ[key]

    def test_get_settings_creates_instance(self) -> None:
        """Test get_settings creates instance."""
        # Set required env var
        os.environ["OPENAI_API_KEY"] = "sk-test123"

        settings = get_settings()
        assert settings is not None
        assert isinstance(settings, Settings)

    def test_get_settings_returns_same_instance(self) -> None:
        """Test get_settings returns singleton."""
        os.environ["OPENAI_API_KEY"] = "sk-test123"

        settings1 = get_settings()
        settings2 = get_settings()
        assert settings1 is settings2

    def test_reset_settings(self) -> None:
        """Test reset_settings clears instance."""
        os.environ["OPENAI_API_KEY"] = "sk-test123"

        settings1 = get_settings()
        reset_settings()
        settings2 = get_settings()
        assert settings1 is not settings2

    def test_settings_from_environment(self) -> None:
        """Test loading settings from environment variables."""
        os.environ["OPENAI_API_KEY"] = "sk-env-key"
        os.environ["OPENAI_MODEL"] = "gpt-4"
        os.environ["DATABASE_HOST"] = "env.host.com"
        os.environ["SECURITY_MAX_ROWS"] = "5000"

        reset_settings()
        settings = get_settings()

        # Use get_secret_value() to access SecretStr content
        assert settings.openai.api_key.get_secret_value() == "sk-env-key"
        assert settings.openai.model == "gpt-4"
        assert settings.database.host == "env.host.com"
        assert settings.security.max_rows == 5000
