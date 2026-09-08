# pyrefly: ignore [missing-import]
from pydantic import BaseModel, Field, model_validator, field_validator
from typing import Literal

# request to validate a netwrk operation
class ValidationRequest(BaseModel):
    operation: Literal["block", "unblock", "bandwidth"] # type of network operation being performed
    client: str # client validation
    rate: str | None = None # bandwidth rate (uused for bandwidth operations)


    # operation and rate validator
    @model_validator(mode="after") 
    def validate_rate(self): 

        # fallback if bandwidth operation are passed without arguements
        if self.operation == "bandwidth" and self.rate is None:
            raise ValueError(
                "Bandwidth validation requires a rate."
            )

        # fallback for operations (other than bandwidth) if pased with arguements
        if self.operation != "bandwidth" and self.rate is not None:
            raise ValueError(
                "Rate is only valid for bandwidth validation."
            )

        return self

    # field validator for rate (bandwidth cmd)
    @field_validator("rate")
    @classmethod
    def validate_rate_format(cls, value):
        if value is None: # allow if rate is empty
            return value

        value = value.lower().strip() # normalize rate string

        valid_units = (
            "kbit",
            "mbit",
            "gbit"
        ) # allowed bandwidth units

        # check for valid bandwidth unit
        if not value.endswith(valid_units):
            raise ValueError(
                "Rate must use kbit, mbit, or gbit."
            )

        unit = next(u for u in valid_units if value.endswith(u))
        try:
            number = float(value[: -len(unit)])
        except ValueError:
            raise ValueError(
                "Rate must contain a numeric value."
            )

        if number <= 0:
            raise ValueError(
                "Rate must be greater than zero."
            )

        return value

# result after validation
class ValidationResult(BaseModel):
    operation: Literal["BLOCK", "UNBLOCK", "BANDWIDTH"] # operation that was validated
    client: str 
    target_ip: str

    ping: str | None = None

    expected_rate: float | None = None
    measured_rate: float | None = None
    tolerance: float | None = None # tolerance for the bandwidth for 

    passed: bool
    message: str

    @model_validator(mode="after")
    def validate_bandwidth_fields(self):
        if self.operation == "BANDWIDTH":
            if self.expected_rate is None or self.measured_rate is None or self.tolerance is None:
                raise ValueError(
                    "Bandwidth results require expected_rate, measured_rate, and tolerance."
                )
        return self
