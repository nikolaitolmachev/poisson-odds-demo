"""Load and validate settings from environment variables and a .env file."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotenv.parser import parse_stream


class ConfigurationError(ValueError):
    """Report invalid application configuration without exposing secrets."""


def _application_directory() -> Path:
    """Locate the executable directory or the source root containing main.py."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Config:
    """Validated application settings.

    Attributes:
        minimal_matches_to_count: Minimum home or away games required for xG.
        model_value_difference: Minimum relative odds difference, in percent.
        proxy: Optional HTTP or SOCKS proxy URL; omitted from repr.
        stats_file: Absolute output path, anchored to the application directory
            when supplied as a relative path.
    """

    minimal_matches_to_count: int = 3
    model_value_difference: int = 30
    proxy: str = field(default="", repr=False)
    stats_file: Path = Path("stats.txt")

    def __post_init__(self) -> None:
        """Validate settings and resolve a relative report path."""
        if self.minimal_matches_to_count < 1:
            raise ConfigurationError("MINIMAL_MATCHES_TO_COUNT must be at least 1.")
        if self.model_value_difference < 0:
            raise ConfigurationError("MODEL_VALUE_DIFFERENCE must be 0 or greater.")
        if self.proxy:
            try:
                parsed = urlsplit(self.proxy)
                port = parsed.port
                valid = (
                    parsed.scheme in {"http", "https", "socks4", "socks4a", "socks5", "socks5h"}
                    and bool(parsed.hostname)
                    and (port is None or port >= 1)
                    and not parsed.netloc.endswith(":")
                    and parsed.path in {"", "/"}
                    and not parsed.query
                    and not parsed.fragment
                    and "\\" not in self.proxy
                    and not any(
                        char.isspace() or ord(char) < 32 or ord(char) == 127 for char in self.proxy
                    )
                )
            except ValueError:
                valid = False
            if not valid:
                raise ConfigurationError("PROXY must be a valid HTTP or SOCKS proxy URL.")
        if not self.stats_file.is_absolute():
            object.__setattr__(self, "stats_file", _application_directory() / self.stats_file)


def _read_env_file(path: Path) -> dict[str, str]:
    """Parse dotenv assignments strictly, without interpolation or secret logging."""
    values: dict[str, str] = {}
    try:
        with path.open(encoding="utf-8-sig") as stream:
            for binding in parse_stream(stream):
                if binding.error or (binding.key is not None and binding.value is None):
                    raise ConfigurationError(
                        f"Invalid .env syntax at line {binding.original.line}."
                    )
                if binding.key is not None and binding.value is not None:
                    if "${" in binding.value:
                        raise ConfigurationError(
                            "Variable interpolation is not supported in .env "
                            f"(line {binding.original.line}). Use literal values."
                        )
                    values[binding.key] = binding.value
    except FileNotFoundError:
        pass
    except (OSError, UnicodeError) as exc:
        raise ConfigurationError("Unable to read the .env file.") from exc
    return values


def load_config() -> Config:
    """Load the application .env file, giving process environment variables priority.

    Read .env from the source root during development or the executable
    directory in a frozen application. A missing .env is allowed; invalid syntax
    and interpolation are rejected.
    Relative STATS_FILE paths use the same directory. Loading does not modify
    the process environment or create output files or directories.

    Returns:
        Validated application configuration.

    Raises:
        ConfigurationError: Syntax or settings are invalid, or .env cannot be read.
    """
    file_values = _read_env_file(_application_directory() / ".env")
    values = {**file_values, **os.environ}

    try:
        minimal_matches = int(values.get("MINIMAL_MATCHES_TO_COUNT", "3"))
    except ValueError as exc:
        raise ConfigurationError("MINIMAL_MATCHES_TO_COUNT must be an integer.") from exc
    try:
        value_difference = int(values.get("MODEL_VALUE_DIFFERENCE", "30"))
    except ValueError as exc:
        raise ConfigurationError("MODEL_VALUE_DIFFERENCE must be an integer.") from exc

    stats_file = values.get("STATS_FILE", "stats.txt").strip()
    if not stats_file or "\x00" in stats_file:
        raise ConfigurationError("STATS_FILE must not be empty or contain a null character.")
    return Config(
        minimal_matches_to_count=minimal_matches,
        model_value_difference=value_difference,
        proxy=values.get("PROXY", ""),
        stats_file=Path(stats_file),
    )
