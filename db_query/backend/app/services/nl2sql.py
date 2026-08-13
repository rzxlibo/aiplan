"""Natural Language to SQL conversion service using OpenAI."""

import logging
import re

from openai import AsyncOpenAI
from app.config import settings
from app.models.database import DatabaseType
from app.services.sql_validator import validate_and_transform_sql, SqlValidationError

logger = logging.getLogger(__name__)


class NaturalLanguageToSQLService:
    """Service for converting natural language queries to SQL using OpenAI."""

    def __init__(self):
        """Initialize OpenAI-compatible client.

        Supports any OpenAI-compatible endpoint via settings.openai_base_url
        (e.g. One-API gateway, DeepSeek, Ollama). Model name is configurable
        via settings.openai_model.
        """
        self.client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url or None,
            timeout=30.0,
        )
        self.model = settings.openai_model

    def _build_prompt(
        self,
        user_prompt: str,
        metadata: dict,
        db_type: DatabaseType = DatabaseType.POSTGRESQL,
        messages: list[dict[str, str]] | None = None,
    ) -> list[dict[str, str]]:
        """Build the prompt for OpenAI with database metadata context.

        Args:
            user_prompt: Natural language query from user
            metadata: Database schema metadata dictionary
            db_type: Database type (PostgreSQL or MySQL)
            messages: Optional multi-turn conversation history (role/content)

        Returns:
            List of messages for OpenAI chat completion
        """
        # Build schema context
        schema_context = []
        for table in metadata.get("tables", []):
            columns_info = []
            for col in table.get("columns", []):
                col_desc = f"  - {col['name']} ({col['dataType']})"
                if col.get("primaryKey"):
                    col_desc += " PRIMARY KEY"
                if not col.get("nullable", True):
                    col_desc += " NOT NULL"
                if col.get("unique"):
                    col_desc += " UNIQUE"
                columns_info.append(col_desc)

            row_count = table.get("rowCount", "unknown")
            table_info = f"Table: {table['schemaName']}.{table['name']} ({row_count} rows)\n"
            table_info += "\n".join(columns_info)
            schema_context.append(table_info)

        for view in metadata.get("views", []):
            columns_info = [f"  - {col['name']} ({col['dataType']})" for col in view.get("columns", [])]
            view_info = f"View: {view['schemaName']}.{view['name']}\n"
            view_info += "\n".join(columns_info)
            schema_context.append(view_info)

        schema_text = "\n\n".join(schema_context)

        # Build database-specific rules
        if db_type == DatabaseType.MYSQL:
            db_name = "MySQL"
            syntax_rules = """3. Use backticks for identifiers (e.g., `table_name`, `column_name`)
4. Return valid MySQL syntax
5. Use MySQL LIMIT syntax (LIMIT n)
6. Be aware of MySQL-specific features like AUTO_INCREMENT"""
        else:
            db_name = "PostgreSQL"
            syntax_rules = """3. Use proper schema qualification (schema.table)
4. Return valid PostgreSQL syntax
5. Use double quotes for identifiers if needed"""

        system_message = f"""You are an expert SQL query generator for {db_name} databases.

Database Schema:
{schema_text}

Rules:
1. Generate ONLY SELECT queries (no INSERT/UPDATE/DELETE/DROP)
2. Always include LIMIT clause (max 1000 rows)
{syntax_rules}
7. Handle both English and Chinese natural language
8. Be concise - keep the natural language explanation to one short sentence
9. First output one sentence of natural language explanation, then the SQL wrapped in a ```sql code block.

Output format:
One sentence of natural language explanation, then the SQL in a ```sql code block."""

        msgs: list[dict[str, str]] = [{"role": "system", "content": system_message}]
        for m in messages or []:
            msgs.append({"role": m["role"], "content": m["content"]})
        msgs.append({"role": "user", "content": user_prompt})
        return msgs

    @staticmethod
    def _parse_response(raw: str) -> dict[str, str]:
        """从模型输出中提取回复文本与 SQL。

        格式约定：一句自然语言说明 + ```sql 代码块。找不到代码块时整段视为 SQL。
        """
        match = re.search(r"```sql\s*(.*?)```", raw, re.DOTALL | re.IGNORECASE)
        if not match:
            match = re.search(r"```\s*(.*?)```", raw, re.DOTALL | re.IGNORECASE)
        if match:
            sql = match.group(1).strip()
            reply = (raw[: match.start()] + raw[match.end():]).strip()
        else:
            sql = raw.strip()
            reply = ""
        return {"reply": reply, "sql": sql}

    @staticmethod
    def _sanitize_error(msg: str, max_len: int = 500) -> str:
        """剥离 HTML 标签、压缩空白并截断，避免整段拦截页抛给前端。"""
        msg = re.sub(r"<[^>]+>", " ", msg)
        msg = re.sub(r"\s+", " ", msg).strip()
        if len(msg) > max_len:
            msg = msg[:max_len] + "...(truncated)"
        return msg

    async def generate_sql(
        self,
        user_prompt: str,
        metadata: dict,
        db_type: DatabaseType = DatabaseType.POSTGRESQL,
        messages: list[dict[str, str]] | None = None,
    ) -> dict[str, str]:
        """Convert natural language to SQL query.

        Args:
            user_prompt: Natural language query
            metadata: Database schema metadata dictionary
            db_type: Database type (PostgreSQL or MySQL)
            messages: Optional multi-turn conversation history (role/content)

        Returns:
            Dict with 'reply' and 'sql' keys

        Raises:
            Exception: If OpenAI API call fails or generated SQL is invalid
        """
        try:
            msgs = self._build_prompt(user_prompt, metadata, db_type, messages)

            # Call OpenAI API
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=msgs,
                temperature=0.1,  # Low temperature for consistent SQL generation
                max_tokens=500,
            )

            generated = response.choices[0].message.content.strip()
            parsed = self._parse_response(generated)

            # Post-generation validation: enforce SELECT-only and a LIMIT cap.
            # Guards against models returning non-SELECT or unbounded queries.
            try:
                parsed["sql"] = validate_and_transform_sql(
                    parsed["sql"], limit=1000, db_type=db_type
                )
            except SqlValidationError as e:
                logger.error(f"Generated SQL failed validation: {e}")
                raise Exception(f"Generated SQL is not valid (SELECT only): {e}")

            logger.info(f"Generated SQL for prompt: {user_prompt[:50]}...")
            return parsed

        except Exception as e:
            message_str = self._sanitize_error(str(e))
            logger.error(f"Failed to generate SQL: {message_str}")
            raise Exception(f"Failed to generate SQL: {message_str}") from e


# Global instance
nl2sql_service = NaturalLanguageToSQLService()
