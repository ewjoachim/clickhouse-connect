import logging

from datetime import datetime
from typing import Any, Dict, List, Optional, Type, TYPE_CHECKING, Tuple

from apispec import APISpec
from apispec.ext.marshmallow import MarshmallowPlugin
from flask_babel import gettext as __
from marshmallow import Schema, fields
from marshmallow.validate import Range
from sqlalchemy.engine.url import URL
from sqlalchemy.sql.type_api import TypeEngine
from superset.db_engine_specs.base import BaseEngineSpec, BasicParametersType, BasicParametersSchema, \
    BasicParametersMixin
from superset.db_engine_specs.exceptions import SupersetDBAPIDatabaseError
from superset.errors import SupersetError, SupersetErrorType, ErrorLevel
from superset.utils import core as utils
from superset.utils.core import GenericDataType

from clickhouse_connect import driver_name
from clickhouse_connect.datatypes import registry
from clickhouse_connect.superset.datatypes import map_generic_types
from superset.models.core import Database

logger = logging.getLogger(__name__)

map_generic_types()


class ClickHouseParametersSchema(Schema):
    username = fields.String(allow_none=True, description=__("Username"))
    password = fields.String(allow_none=True, description=__("Password"))
    host = fields.String(required=True, description=__("Hostname or IP address"))
    port = fields.Integer(allow_none=True, description=__("Database port"), validate=Range(min=0, max=65535), )
    database = fields.String(allow_none=True, description=__("Database name"))
    encryption = fields.Boolean(default=True, description=__("Use an encrypted connection to the database"))
    query = fields.Dict(keys=fields.Str(), values=fields.Raw(), description=__("Additional parameters"))


class ClickHouseEngineSpec(BaseEngineSpec, BasicParametersMixin):
    engine = driver_name
    engine_name = "ClickHouse Connect"

    default_driver = 'connect'
    time_secondary_columns = True
    time_groupby_inline = True
    _function_names = []

    _time_grain_expressions = {
        None: "{col}",
        "PT1M": "toStartOfMinute(toDateTime({col}))",
        "PT5M": "toStartOfFiveMinutes(toDateTime({col}))",
        "PT10M": "toStartOfTenMinutes(toDateTime({col}))",
        "PT15M": "toStartOfFifteenMinutes(toDateTime({col}))",
        "PT30M": "toDateTime(intDiv(toUInt32(toDateTime({col})), 1800)*1800)",
        "PT1H": "toStartOfHour(toDateTime({col}))",
        "P1D": "toStartOfDay(toDateTime({col}))",
        "P1W": "toMonday(toDateTime({col}))",
        "P1M": "toStartOfMonth(toDateTime({col}))",
        "P3M": "toStartOfQuarter(toDateTime({col}))",
        "P1Y": "toStartOfYear(toDateTime({col}))",
    }

    sqlalchemy_uri_placeholder = "clickhousedb+connect://user:password@host:port/dbname[?secure=value&=value...]"
    parameters_schema = ClickHouseParametersSchema()
    encryption_parameters = {'secure': 'true'}

    @classmethod
    def epoch_to_dttm(cls) -> str:
        return '{col}'

    @classmethod
    def get_dbapi_exception_mapping(cls) -> Dict[Type[Exception], Type[Exception]]:
        return {}

    @classmethod
    def get_dbapi_mapped_exception(cls, exception: Exception) -> Exception:
        new_exception = cls.get_dbapi_exception_mapping().get(type(exception))
        if new_exception == SupersetDBAPIDatabaseError:
            return SupersetDBAPIDatabaseError("Connection failed")
        if not new_exception:
            return exception
        return new_exception(str(exception))

    @classmethod
    def convert_dttm(cls, target_type: str, dttm: datetime, db_extra: Optional[Dict[str, Any]] = None) \
            -> Optional[str]:
        tt = target_type.upper()
        if tt == utils.TemporalType.DATE:
            return f"toDate('{dttm.date().isoformat()}')"
        if tt == utils.TemporalType.DATETIME:
            return f"""toDateTime('{dttm.isoformat(sep=" ", timespec="seconds")}')"""
        return None

    @classmethod
    def get_function_names(cls, database: 'Database') -> List[str]:
        if cls._function_names:
            return cls._function_names
        try:
            names = (database.get_df(
                'SELECT name FROM system.functions UNION ALL SELECT name FROM system.table_functions')['name'].tolist())
            cls._function_names = names
            return names
        except Exception:
            logger.exception('Error retrieving system.functions')
            return []

    @classmethod
    def get_sqla_column_type(cls, column_type: Optional[str], *args, **kwargs) \
            -> Optional[Tuple[TypeEngine, GenericDataType]]:
        if column_type is None:
            return None
        ch_type = registry.get_from_name(column_type)
        return ch_type.get_sqla_type(), ch_type.generic_type

    @classmethod
    def column_datatype_to_string(cls, sqla_column_type: TypeEngine, *args):
        return sqla_column_type.compile()

    @classmethod
    def build_sqlalchemy_uri(cls, parameters: BasicParametersType, encrypted_extra: Optional[Dict[str, str]] = None):
        url_params = parameters.copy()
        if 'encryption' in parameters:
            query = parameters.get("query", {}).copy()
            query.update(cls.encryption_parameters)
            del url_params['encryption']
        return str(URL(f'{cls.engine}+{cls.default_driver}', **url_params))

    @classmethod
    def validate_parameters(cls, parameters: BasicParametersType) -> List[SupersetError]:
        errors: List[SupersetError] = []
        host = parameters.get("host", None)
        if not host:
            errors.append(SupersetError(
                message='Hostname is required',
                error_type=SupersetErrorType.CONNECTION_MISSING_PARAMETERS_ERROR,
                level=ErrorLevel.WARNING,
                extra={"missing": ['host']},
            ))
            return errors
        return errors

