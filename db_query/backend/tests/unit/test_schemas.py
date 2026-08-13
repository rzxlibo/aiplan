"""Unit tests for API schemas."""

from app.models.schemas import ChatMessage, NaturalLanguageInput, GeneratedSqlResponse


def test_chat_message_schema():
    msg = ChatMessage(role="user", content="show users")
    assert msg.role == "user"
    assert msg.content == "show users"


def test_natural_language_input_messages_default_empty():
    req = NaturalLanguageInput(prompt="show users")
    assert req.messages == []


def test_natural_language_input_with_messages():
    req = NaturalLanguageInput(
        prompt="show orders",
        messages=[ChatMessage(role="user", content="show users")],
    )
    assert req.prompt == "show orders"
    assert len(req.messages) == 1


def test_generated_sql_response_reply_field():
    resp = GeneratedSqlResponse(reply="Here you go", sql="SELECT 1")
    assert resp.reply == "Here you go"
    assert resp.sql == "SELECT 1"
