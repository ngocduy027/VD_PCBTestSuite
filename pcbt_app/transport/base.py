"""Abstract CAN transport interface implemented by every adapter backend."""

from __future__ import annotations

from abc import ABC, abstractmethod

from pcbt_app.models import RawCanFrame


class CanTransportError(RuntimeError):
    """Raised when the CAN transport fails to send or receive data."""


class AbstractCanTransport(ABC):
    """Interface every CAN backend implements.

    Methods raise :class:`CanTransportError` on adapter failures; services catch
    that and turn it into a FAIL verdict. Optional extras some backends add and
    the service detects with ``getattr``: ``reset_stream()`` (drop partially
    parsed bytes) and ``take_parser_stats()`` (serial-stream error summary).
    """

    @abstractmethod
    def connect(self) -> None:
        """Open and configure the adapter; no-op if already connected."""
        raise NotImplementedError

    @abstractmethod
    def disconnect(self) -> None:
        """Close the adapter; safe to call when not connected."""
        raise NotImplementedError

    @abstractmethod
    def is_connected(self) -> bool:
        """True while the adapter is open."""
        raise NotImplementedError

    @abstractmethod
    def send(self, frame: RawCanFrame) -> None:
        """Transmit one frame."""
        raise NotImplementedError

    @abstractmethod
    def recv(self, timeout_s: float) -> RawCanFrame | None:
        """Return the next received frame, waiting up to ``timeout_s``; ``None`` on timeout."""
        raise NotImplementedError

    @abstractmethod
    def drain(self) -> list[RawCanFrame]:
        """Return all frames already received without waiting."""
        raise NotImplementedError
