from __future__ import annotations

from abc import ABC, abstractmethod

from pcbt_app.models import RawCanFrame


class CanTransportError(RuntimeError):
    """Raised when the CAN transport fails to send or receive data."""


class AbstractCanTransport(ABC):
    @abstractmethod
    def connect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def disconnect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def is_connected(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def send(self, frame: RawCanFrame) -> None:
        raise NotImplementedError

    @abstractmethod
    def recv(self, timeout_s: float) -> RawCanFrame | None:
        raise NotImplementedError

    @abstractmethod
    def drain(self) -> list[RawCanFrame]:
        raise NotImplementedError
