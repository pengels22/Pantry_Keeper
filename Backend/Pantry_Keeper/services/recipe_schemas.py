"""Strict request boundaries shared by HTTP routes and model tool execution."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, field_validator

Amount = Annotated[FiniteFloat, Field(ge=0, strict=True)]
PositiveAmount = Annotated[FiniteFloat, Field(gt=0, strict=True)]
InventoryID = Annotated[int, Field(gt=0, strict=True)]
Unit = Annotated[str, Field(min_length=1, max_length=32)]


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Usage(RequestModel):
    inventory_id: InventoryID
    name: str = Field(default="", max_length=255)
    amount: PositiveAmount
    unit: Unit
    notes: str = Field(default="", max_length=2000)


class RecipeProposal(RequestModel):
    recipe: str = Field(min_length=1, max_length=255)
    instructions: str = Field(default="", max_length=20000)
    ingredients: list[Usage] = Field(min_length=1, max_length=100)


class SessionRequest(RequestModel):
    proposal: RecipeProposal | None = None
    title: str | None = Field(default=None, min_length=1, max_length=255)


class ActualUsage(RequestModel):
    inventory_id: InventoryID
    amount: Amount
    unit: Unit


class ConfirmedRequest(RequestModel):
    confirmed: Literal[True]

    @field_validator("confirmed", mode="before")
    @classmethod
    def explicit_boolean(cls, value):
        if value is not True:
            raise ValueError("Explicit boolean true is required.")
        return value


class CommitRequest(ConfirmedRequest):
    actual_usage: list[ActualUsage] = Field(min_length=1, max_length=100)


class Measurements(RequestModel):
    package_quantity: Amount | None = None
    package_size: PositiveAmount | None = None
    package_unit: Unit | None = None
    usable_quantity: Amount | None = None
    usable_unit: Unit


class Adjustment(ConfirmedRequest):
    amount: Amount
    unit: Unit
    reason: str = Field(default="Manual inventory adjustment", max_length=2000)


class ChatMessage(RequestModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=10000)


class ChatRequest(RequestModel):
    message: str = Field(min_length=1, max_length=10000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=20)
