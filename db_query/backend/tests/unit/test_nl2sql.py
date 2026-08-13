"""Unit tests for natural language to SQL conversion service."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.services.nl2sql import NaturalLanguageToSQLService


@pytest.fixture
def nl2sql_service():
    """Create NaturalLanguageToSQLService instance."""
    return NaturalLanguageToSQLService()


@pytest.fixture
def sample_metadata():
    """Sample database metadata."""
    return {
        "tables": [
            {
                "name": "users",
                "schemaName": "public",
                "rowCount": 100,
                "columns": [
                    {
                        "name": "id",
                        "dataType": "integer",
                        "nullable": False,
                        "primaryKey": True,
                        "unique": False,
                        "defaultValue": None,
                    },
                    {
                        "name": "name",
                        "dataType": "character varying",
                        "nullable": False,
                        "primaryKey": False,
                        "unique": False,
                        "defaultValue": None,
                    },
                    {
                        "name": "email",
                        "dataType": "character varying",
                        "nullable": False,
                        "primaryKey": False,
                        "unique": True,
                        "defaultValue": None,
                    },
                ],
            },
            {
                "name": "orders",
                "schemaName": "public",
                "rowCount": 500,
                "columns": [
                    {
                        "name": "id",
                        "dataType": "integer",
                        "nullable": False,
                        "primaryKey": True,
                        "unique": False,
                        "defaultValue": None,
                    },
                    {
                        "name": "user_id",
                        "dataType": "integer",
                        "nullable": False,
                        "primaryKey": False,
                        "unique": False,
                        "defaultValue": None,
                    },
                    {
                        "name": "total",
                        "dataType": "numeric",
                        "nullable": False,
                        "primaryKey": False,
                        "unique": False,
                        "defaultValue": None,
                    },
                ],
            },
        ],
        "views": [
            {
                "name": "user_summary",
                "schemaName": "public",
                "columns": [
                    {"name": "total_users", "dataType": "bigint"},
                ],
            }
        ],
    }


class TestGenerateSql:
    """Test SQL generation from natural language."""

    @pytest.mark.asyncio
    async def test_generate_sql_basic_query(self, nl2sql_service, sample_metadata):
        """Test generating SQL from basic natural language query."""
        # Mock OpenAI response
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(
                    content="Here are the users.\n```sql\nSELECT * FROM public.users LIMIT 100\n```"
                )
            )
        ]

        with patch.object(
            nl2sql_service.client.chat.completions,
            "create",
            new=AsyncMock(return_value=mock_response),
        ) as mock_create:
            result = await nl2sql_service.generate_sql(
                user_prompt="Show me all users",
                metadata=sample_metadata,
            )

            # Verify OpenAI was called
            mock_create.assert_called_once()
            call_args = mock_create.call_args

            # Verify result structure
            assert "sql" in result
            assert "reply" in result
            assert result["sql"] == "SELECT * FROM public.users LIMIT 100"
            assert result["reply"] == "Here are the users."

            # Verify OpenAI call parameters (configured model passed through)
            assert call_args.kwargs["model"] == nl2sql_service.model

    @pytest.mark.asyncio
    async def test_generate_sql_removes_markdown(self, nl2sql_service, sample_metadata):
        """Test that generated SQL removes markdown code blocks."""
        # Mock OpenAI response with markdown
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(
                    content="```sql\nSELECT * FROM public.users LIMIT 100\n```"
                )
            )
        ]

        with patch.object(
            nl2sql_service.client.chat.completions,
            "create",
            new=AsyncMock(return_value=mock_response),
        ):
            result = await nl2sql_service.generate_sql(
                user_prompt="Show me all users",
                metadata=sample_metadata,
            )

            # Should have removed markdown
            assert result["sql"] == "SELECT * FROM public.users LIMIT 100"
            assert "```" not in result["sql"]

    @pytest.mark.asyncio
    async def test_generate_sql_removes_generic_markdown(self, nl2sql_service, sample_metadata):
        """Test removing generic markdown code blocks."""
        # Mock OpenAI response with generic markdown
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(
                    content="```\nSELECT id, name FROM public.users WHERE id > 10 LIMIT 50\n```"
                )
            )
        ]

        with patch.object(
            nl2sql_service.client.chat.completions,
            "create",
            new=AsyncMock(return_value=mock_response),
        ):
            result = await nl2sql_service.generate_sql(
                user_prompt="Get users with id greater than 10",
                metadata=sample_metadata,
            )

            assert result["sql"] == "SELECT id, name FROM public.users WHERE id > 10 LIMIT 50"
            assert "```" not in result["sql"]

    @pytest.mark.asyncio
    async def test_generate_sql_with_chinese_prompt(self, nl2sql_service, sample_metadata):
        """Test generating SQL from Chinese natural language."""
        # Mock OpenAI response
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(
                    content="这里是所有用户。\n```sql\nSELECT * FROM public.users LIMIT 100\n```"
                )
            )
        ]

        with patch.object(
            nl2sql_service.client.chat.completions,
            "create",
            new=AsyncMock(return_value=mock_response),
        ):
            result = await nl2sql_service.generate_sql(
                user_prompt="显示所有用户",
                metadata=sample_metadata,
            )

            assert "sql" in result
            assert result["sql"] == "SELECT * FROM public.users LIMIT 100"

    @pytest.mark.asyncio
    async def test_generate_sql_with_join(self, nl2sql_service, sample_metadata):
        """Test generating SQL with JOIN."""
        # Mock OpenAI response with JOIN
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(
                    content="Here are users with their orders.\n```sql\nSELECT u.name, o.total FROM public.users u JOIN public.orders o ON u.id = o.user_id LIMIT 100\n```"
                )
            )
        ]

        with patch.object(
            nl2sql_service.client.chat.completions,
            "create",
            new=AsyncMock(return_value=mock_response),
        ):
            result = await nl2sql_service.generate_sql(
                user_prompt="Show me users with their orders",
                metadata=sample_metadata,
            )

            assert "JOIN" in result["sql"]
            assert "users" in result["sql"]
            assert "orders" in result["sql"]

    @pytest.mark.asyncio
    async def test_generate_sql_handles_api_error(self, nl2sql_service, sample_metadata):
        """Test that API errors are properly raised."""
        # Mock OpenAI to raise error
        with patch.object(
            nl2sql_service.client.chat.completions,
            "create",
            new=AsyncMock(side_effect=Exception("OpenAI API error")),
        ):
            with pytest.raises(Exception) as exc_info:
                await nl2sql_service.generate_sql(
                    user_prompt="Show me all users",
                    metadata=sample_metadata,
                )

            assert "Failed to generate SQL" in str(exc_info.value)
            assert "OpenAI API error" in str(exc_info.value)


class TestBuildPrompt:
    """Test prompt building for OpenAI."""

    def test_build_prompt_includes_schema(self, nl2sql_service, sample_metadata):
        """Test that prompt includes database schema information."""
        messages = nl2sql_service._build_prompt(
            user_prompt="Show me all users",
            metadata=sample_metadata,
        )

        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "Show me all users"

        # Check system message contains schema
        system_message = messages[0]["content"]
        assert "users" in system_message
        assert "orders" in system_message
        assert "public" in system_message
        assert "100 rows" in system_message
        assert "500 rows" in system_message

        # Check column information
        assert "id" in system_message
        assert "name" in system_message
        assert "email" in system_message
        assert "PRIMARY KEY" in system_message
        assert "UNIQUE" in system_message

    def test_build_prompt_includes_views(self, nl2sql_service, sample_metadata):
        """Test that prompt includes view information."""
        messages = nl2sql_service._build_prompt(
            user_prompt="Query views",
            metadata=sample_metadata,
        )

        system_message = messages[0]["content"]
        assert "View:" in system_message
        assert "user_summary" in system_message
        assert "total_users" in system_message

    def test_build_prompt_includes_rules(self, nl2sql_service, sample_metadata):
        """Test that prompt includes generation rules."""
        messages = nl2sql_service._build_prompt(
            user_prompt="Test query",
            metadata=sample_metadata,
        )

        system_message = messages[0]["content"]
        assert "Rules:" in system_message
        assert "SELECT" in system_message
        assert "LIMIT" in system_message
        assert "PostgreSQL" in system_message
        assert "English and Chinese" in system_message

    def test_build_prompt_with_nullable_columns(self, nl2sql_service):
        """Test prompt building with nullable and not null columns."""
        metadata = {
            "tables": [
                {
                    "name": "products",
                    "schemaName": "public",
                    "rowCount": 50,
                    "columns": [
                        {
                            "name": "id",
                            "dataType": "integer",
                            "nullable": False,
                            "primaryKey": True,
                            "unique": False,
                        },
                        {
                            "name": "description",
                            "dataType": "text",
                            "nullable": True,
                            "primaryKey": False,
                            "unique": False,
                        },
                    ],
                }
            ],
            "views": [],
        }

        messages = nl2sql_service._build_prompt(
            user_prompt="Test",
            metadata=metadata,
        )

        system_message = messages[0]["content"]
        assert "NOT NULL" in system_message
        # nullable=True should not add NOT NULL

    def test_build_prompt_empty_metadata(self, nl2sql_service):
        """Test prompt building with empty metadata."""
        metadata = {"tables": [], "views": []}

        messages = nl2sql_service._build_prompt(
            user_prompt="Test query",
            metadata=metadata,
        )

        assert len(messages) == 2
        system_message = messages[0]["content"]
        assert "Database Schema:" in system_message

    def test_build_prompt_with_default_values(self, nl2sql_service):
        """Test prompt building includes default values."""
        metadata = {
            "tables": [
                {
                    "name": "settings",
                    "schemaName": "public",
                    "rowCount": 10,
                    "columns": [
                        {
                            "name": "enabled",
                            "dataType": "boolean",
                            "nullable": False,
                            "primaryKey": False,
                            "unique": False,
                            "defaultValue": "true",
                        },
                    ],
                }
            ],
            "views": [],
        }

        messages = nl2sql_service._build_prompt(
            user_prompt="Test",
            metadata=metadata,
        )

        system_message = messages[0]["content"]
        assert "settings" in system_message
        assert "enabled" in system_message
        assert "boolean" in system_message


class TestGeneratedSqlValidation:
    """Test post-generation SQL validation."""

    @pytest.mark.asyncio
    async def test_generate_sql_adds_limit_when_missing(self, nl2sql_service, sample_metadata):
        """SELECT without LIMIT gets LIMIT 1000 appended by validation."""
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content="SELECT id FROM users"))
        ]

        with patch.object(
            nl2sql_service.client.chat.completions,
            "create",
            new=AsyncMock(return_value=mock_response),
        ):
            result = await nl2sql_service.generate_sql(
                user_prompt="list all users",
                metadata=sample_metadata,
            )

            assert "LIMIT" in result["sql"].upper()
            assert "1000" in result["sql"]

    @pytest.mark.asyncio
    async def test_generate_sql_rejects_non_select(self, nl2sql_service, sample_metadata):
        """Non-SELECT generated SQL raises a clear error."""
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content="DELETE FROM users"))
        ]

        with patch.object(
            nl2sql_service.client.chat.completions,
            "create",
            new=AsyncMock(return_value=mock_response),
        ):
            with pytest.raises(Exception, match="SELECT only"):
                await nl2sql_service.generate_sql(
                    user_prompt="delete all users",
                    metadata=sample_metadata,
                )


class TestMultiTurn:
    """Test multi-turn history and reply parsing."""

    @pytest.mark.asyncio
    async def test_generate_sql_parses_reply_and_sql(self, nl2sql_service, sample_metadata):
        """自然句 + SQL 代码块被正确拆成 reply 和 sql。"""
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(
                    content="Here are the active users.\n```sql\nSELECT id, name FROM public.users WHERE active = true LIMIT 100\n```"
                )
            )
        ]

        with patch.object(
            nl2sql_service.client.chat.completions,
            "create",
            new=AsyncMock(return_value=mock_response),
        ):
            result = await nl2sql_service.generate_sql("show active users", sample_metadata)

        assert result["reply"] == "Here are the active users."
        assert result["sql"] == "SELECT id, name FROM public.users WHERE active = true LIMIT 100"

    @pytest.mark.asyncio
    async def test_generate_sql_includes_history(self, nl2sql_service, sample_metadata):
        """历史消息进入 prompt，当前输入作为最后一条 user 消息。"""
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content="```sql\nSELECT * FROM public.orders LIMIT 100\n```"))
        ]

        history = [
            {"role": "user", "content": "show all users"},
            {"role": "assistant", "content": "SELECT * FROM public.users LIMIT 100"},
        ]

        with patch.object(
            nl2sql_service.client.chat.completions,
            "create",
            new=AsyncMock(return_value=mock_response),
        ) as mock_create:
            await nl2sql_service.generate_sql("now show orders", sample_metadata, messages=history)

        sent = mock_create.call_args.kwargs["messages"]
        roles = [m["role"] for m in sent]
        assert roles == ["system", "user", "assistant", "user"]
        assert sent[1]["content"] == "show all users"
        assert sent[2]["content"] == "SELECT * FROM public.users LIMIT 100"
        assert sent[-1]["content"] == "now show orders"

    @pytest.mark.asyncio
    async def test_generate_sql_sanitizes_html_error(self, nl2sql_service, sample_metadata):
        """拦截页 HTML 不应整段透传给调用方。"""
        html_error = (
            "<!DOCTYPE html><html><body><title>Error - Request Blocked</title>"
            "<p>Some long body text</p></body></html>"
        )
        with patch.object(
            nl2sql_service.client.chat.completions,
            "create",
            new=AsyncMock(side_effect=Exception(html_error)),
        ):
            with pytest.raises(Exception) as exc_info:
                await nl2sql_service.generate_sql("show users", sample_metadata)

        msg = str(exc_info.value)
        assert "Failed to generate SQL" in msg
        assert "<html" not in msg
        assert "<title>" not in msg

    def test_build_prompt_includes_history(self, nl2sql_service, sample_metadata):
        """_build_prompt 组装 system + 历史 + 当前 user。"""
        messages = nl2sql_service._build_prompt(
            user_prompt="show orders",
            metadata=sample_metadata,
            messages=[
                {"role": "user", "content": "show users"},
                {"role": "assistant", "content": "SELECT * FROM public.users LIMIT 100"},
            ],
        )
        roles = [m["role"] for m in messages]
        assert roles == ["system", "user", "assistant", "user"]
        assert messages[1]["content"] == "show users"
        assert messages[2]["content"] == "SELECT * FROM public.users LIMIT 100"
        assert messages[3]["content"] == "show orders"
