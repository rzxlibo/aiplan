"""装配层测试:验证 lifespan 按配置的数据库集合构建运行时组件。

回归背景:多数据库与安全控制曾"设计有承诺、装配未启用"——server.py 恒只建
一个 executor,并把 blocked_tables / blocked_columns / allow_explain 硬编码为
None / None / False,导致黑名单恒为空集、敏感对象零保护。

装配层此前零测试覆盖,是这些缺陷长期隐藏的根本原因,因此本文件单独针对
"配置值是否真正抵达运行时组件"做断言。
"""

from contextlib import ExitStack, asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import pg_mcp.server as server
from pg_mcp.cache.schema_cache import SchemaCache
from pg_mcp.config.settings import (
    DatabaseConfig,
    OpenAIConfig,
    ResilienceConfig,
    SecurityConfig,
    Settings,
    ValidationConfig,
)
from pg_mcp.models.errors import ErrorCode, SecurityViolationError
from pg_mcp.models.schema import DatabaseSchema
from pg_mcp.resilience.rate_limiter import RateLimiter

TWO_DATABASES = [
    DatabaseConfig(name="blog"),
    DatabaseConfig(name="shop"),
]

EMPTY_SCHEMA = DatabaseSchema(database_name="test", tables=[], version="15.0")


def _settings(
    databases: list[DatabaseConfig],
    security: SecurityConfig | None = None,
    resilience: ResilienceConfig | None = None,
    validation: ValidationConfig | None = None,
) -> Settings:
    """构造测试用 Settings,用 _env_file=None 隔离本机真实 .env。"""
    return Settings(
        _env_file=None,
        openai=OpenAIConfig(api_key="sk-test"),
        database=databases[0],
        databases=databases,
        security=security or SecurityConfig(_env_file=None),
        resilience=resilience or ResilienceConfig(),
        validation=validation or ValidationConfig(),
    )


def _fake_pools(databases: list[DatabaseConfig]) -> dict[str, MagicMock]:
    return {db.name: MagicMock() for db in databases}


@asynccontextmanager
async def running_server(settings: Settings, pools: dict[str, MagicMock]):
    """跑一遍完整的 lifespan,只把真实数据库 I/O 换成替身。

    create_pools 用 create=True:该名字在未修复的 server.py 中尚不存在,
    这样测试体在修复前后都能真正执行,失败信号来自断言而不是 patch 报错。
    """
    with ExitStack() as stack:
        stack.enter_context(patch("pg_mcp.server.Settings", return_value=settings))
        stack.enter_context(
            patch("pg_mcp.server.create_pools", new=AsyncMock(return_value=pools), create=True)
        )
        stack.enter_context(patch("pg_mcp.server.close_pools", new=AsyncMock(), create=True))
        stack.enter_context(
            patch.object(SchemaCache, "load", new=AsyncMock(return_value=EMPTY_SCHEMA))
        )
        async with server.lifespan(MagicMock()):
            yield


class TestMultiDatabaseWiring:
    """配置了多个库时,装配层必须为每个库建池并配上执行器。"""

    @pytest.mark.asyncio
    async def test_one_pool_per_configured_database(self) -> None:
        pools = _fake_pools(TWO_DATABASES)

        async with running_server(_settings(TWO_DATABASES), pools):
            assert set(server._pools) == {"blog", "shop"}

    @pytest.mark.asyncio
    async def test_one_executor_per_configured_database(self) -> None:
        pools = _fake_pools(TWO_DATABASES)

        async with running_server(_settings(TWO_DATABASES), pools):
            assert server._orchestrator is not None
            assert set(server._orchestrator.sql_executors) == {"blog", "shop"}

    @pytest.mark.asyncio
    async def test_each_executor_is_bound_to_its_own_pool(self) -> None:
        """executor 必须绑到自己那个库的池,否则 SQL 会打到错误的库。"""
        pools = _fake_pools(TWO_DATABASES)

        async with running_server(_settings(TWO_DATABASES), pools):
            executors = server._orchestrator.sql_executors
            assert executors["blog"].pool is pools["blog"]
            assert executors["shop"].pool is pools["shop"]

    @pytest.mark.asyncio
    async def test_single_database_config_still_works(self) -> None:
        """向后兼容:只配 database 单数时,回落为单库。"""
        single = [DatabaseConfig(name="only")]
        pools = _fake_pools(single)
        settings = Settings(
            _env_file=None,  # 否则本机 .env 的 DATABASES 会覆盖掉"只配单库"这个前提
            openai=OpenAIConfig(api_key="sk-test"),
            database=single[0],
        )

        async with running_server(settings, pools):
            assert set(server._orchestrator.sql_executors) == {"only"}


class TestSecurityConfigWiring:
    """配置里的安全策略必须真正抵达 SQLValidator。"""

    @pytest.mark.asyncio
    async def test_blocked_tables_from_config_are_enforced(self) -> None:
        settings = _settings(TWO_DATABASES, SecurityConfig(blocked_tables=["users"]))
        pools = _fake_pools(TWO_DATABASES)

        async with running_server(settings, pools):
            validator = server._orchestrator.sql_validator
            assert validator.blocked_tables == {"users"}
            with pytest.raises(SecurityViolationError):
                validator.validate_or_raise("SELECT * FROM users")

    @pytest.mark.asyncio
    async def test_blocked_columns_from_config_are_enforced(self) -> None:
        settings = _settings(TWO_DATABASES, SecurityConfig(blocked_columns=["password"]))
        pools = _fake_pools(TWO_DATABASES)

        async with running_server(settings, pools):
            validator = server._orchestrator.sql_validator
            assert validator.blocked_columns == {"password"}
            with pytest.raises(SecurityViolationError):
                validator.validate_or_raise("SELECT password FROM accounts")

    @pytest.mark.asyncio
    async def test_allow_explain_defaults_to_disabled(self) -> None:
        pools = _fake_pools(TWO_DATABASES)

        async with running_server(_settings(TWO_DATABASES), pools):
            validator = server._orchestrator.sql_validator
            assert validator.allow_explain is False
            with pytest.raises(SecurityViolationError):
                validator.validate_or_raise("EXPLAIN SELECT 1")

    @pytest.mark.asyncio
    async def test_allow_explain_from_config_reaches_the_validator(self) -> None:
        settings = _settings(TWO_DATABASES, SecurityConfig(allow_explain=True))
        pools = _fake_pools(TWO_DATABASES)

        async with running_server(settings, pools):
            validator = server._orchestrator.sql_validator
            assert validator.allow_explain is True
            validator.validate_or_raise("EXPLAIN SELECT 1")


class TestMetricsWiring:
    """MetricsCollector 必须按设计注入到各服务。

    设计文档在 SQLGenerator / SQLExecutor / ResultValidator 的构造签名里都写了
    `metrics: MetricsCollector | None = None`; 实现把参数整个删掉了,
    server.py 只是 new 了一个实例后弃之不用。
    """

    @pytest.mark.asyncio
    async def test_metrics_instance_reaches_every_service(self) -> None:
        pools = _fake_pools(TWO_DATABASES)

        async with running_server(_settings(TWO_DATABASES), pools):
            metrics = server._metrics
            orchestrator = server._orchestrator

            assert metrics is not None
            assert orchestrator.metrics is metrics
            assert orchestrator.sql_generator.metrics is metrics
            assert orchestrator.result_validator.metrics is metrics
            assert orchestrator.sql_executors["blog"].metrics is metrics
            assert orchestrator.sql_executors["shop"].metrics is metrics


class TestResilienceWiring:
    """弹性组件必须真的接入请求流程。

    回归背景: server.py 曾 new 出 MetricsCollector / MultiRateLimiter / CircuitBreaker
    后一处都不用; 生效的熔断器其实是 orchestrator 自己另建的那个。
    """

    @pytest.mark.asyncio
    async def test_query_limiter_is_acquired_on_every_query(self) -> None:
        pools = _fake_pools(TWO_DATABASES)

        async with running_server(_settings(TWO_DATABASES), pools):
            orchestrator = server._orchestrator
            orchestrator.sql_generator = AsyncMock()
            orchestrator.sql_generator.generate.return_value = "SELECT 1"

            with patch.object(
                RateLimiter, "acquire", new=AsyncMock(return_value=True)
            ) as spy:
                result = await server.query(
                    question="anything", database="blog", return_type="sql"
                )

        assert result["success"] is True
        assert spy.await_count >= 1

    @pytest.mark.asyncio
    async def test_llm_limiter_is_injected_into_orchestrator(self) -> None:
        pools = _fake_pools(TWO_DATABASES)

        async with running_server(_settings(TWO_DATABASES), pools):
            assert server._orchestrator.llm_limiter is server._rate_limiter.llm_limiter

    @pytest.mark.asyncio
    async def test_concurrency_limits_come_from_config(self) -> None:
        """限额必须来自 ResilienceConfig, 而不是硬编码的 10/5。"""
        settings = _settings(
            TWO_DATABASES, resilience=ResilienceConfig(query_limit=3, llm_limit=2)
        )
        pools = _fake_pools(TWO_DATABASES)

        async with running_server(settings, pools):
            assert server._rate_limiter.query_limiter.available == 3
            assert server._rate_limiter.llm_limiter.available == 2

    def test_server_level_circuit_breaker_removed(self) -> None:
        """服务层那个从未被使用的熔断器实例已删除。

        真正生效的是 QueryOrchestrator 内部自建的那个。
        """
        assert not hasattr(server, "_circuit_breaker")

    @pytest.mark.asyncio
    async def test_overlong_question_is_rejected(self) -> None:
        """max_question_length 必须生效, 并返回 QUESTION_TOO_LONG。"""
        settings = _settings(
            TWO_DATABASES, validation=ValidationConfig(max_question_length=10)
        )
        pools = _fake_pools(TWO_DATABASES)

        async with running_server(settings, pools):
            result = await server.query(question="x" * 50, database="blog")

        assert result["success"] is False
        assert result["error"]["code"] == ErrorCode.QUESTION_TOO_LONG.value
