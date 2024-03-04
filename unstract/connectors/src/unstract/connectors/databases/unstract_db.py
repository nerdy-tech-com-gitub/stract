import logging
from abc import ABC, abstractmethod
from typing import Any

from unstract.connectors.base import UnstractConnector
from unstract.connectors.enums import ConnectorMode
from unstract.connectors.exceptions import ConnectorError


class UnstractDB(UnstractConnector, ABC):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(filename)s - %(message)s",
    )

    def __init__(self, name: str):
        super().__init__(name)
        self.name = name

    @staticmethod
    def get_id() -> str:
        return ""

    @staticmethod
    def get_name() -> str:
        return ""

    @staticmethod
    def get_description() -> str:
        return ""

    @staticmethod
    def get_icon() -> str:
        return ""

    @staticmethod
    def get_json_schema() -> str:
        return ""

    @staticmethod
    def can_write() -> bool:
        return False

    @staticmethod
    def can_read() -> bool:
        return False

    @staticmethod
    def get_connector_mode() -> ConnectorMode:
        return ConnectorMode.DATABASE

    @staticmethod
    def requires_oauth() -> bool:
        return False

    # TODO: Can be removed if removed from base class
    @staticmethod
    def python_social_auth_backend() -> str:
        return ""

    @abstractmethod
    def get_engine(self) -> Any:
        pass

    def test_credentials(self) -> bool:
        """To test credentials for a DB connector."""
        try:
            self.get_engine()
        except Exception as e:
            raise ConnectorError(str(e))
        return True