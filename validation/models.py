# pyrefly: ignore [missing-import]
from pydantic import BaseModel, Field, model_validator, field_validator
from typing import Literal

class ValidationRequest(BaseModel):
    operation: Literal["block", "unblock", "bandwidth"]
    client: str
    rate: str | None = None

    @model_validator(mode="after")
    def validate_rate(self):
        if self.operation == "bandwidth" and self.rate is None:
            raise ValueError(
                "Bandwidth validation requires a rate."
            )

        if self.operation != "bandwidth" and self.rate is not None:
            raise ValueError(
                "Rate is only valid for bandwidth validation."
            )

        return self

    @field_validator("rate")
    @classmethod
    def validate_rate_format(cls, value):
        if value is None:
            return value

        value = value.lower().strip()

        valid_units = (
            "kbit",
            "mbit",
            "gbit"
        )

        if not value.endswith(valid_units):
            raise ValueError(
                "Rate must use kbit, mbit, or gbit."
            )

        try:
            number = float(
                value[:-4]
            )
        except ValueError:
            raise ValueError(
                "Rate must contain a numeric value."
            )

        if number <= 0:
            raise ValueError(
                "Rate must be greater than zero."
            )

        return value

class ValidationResult(BaseModel):
    operation: Literal["BLOCK", "UNBLOCK", "BANDWIDTH"]
    client: str
    target_ip: str

    ping: str | None = None

    expected_rate: float | None = None
    measured_rate: float | None = None
    tolerance: float | None = None

    passed: bool
    message: str
