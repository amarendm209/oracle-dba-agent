import pytest

from oracle_dba_agent.db import query


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM app.orders",
        "update app.orders set qty = 1",
        "BEGIN NULL; END;",
        "drop table app.orders",
    ],
)
def test_write_statements_are_rejected(sql):
    with pytest.raises(ValueError):
        query(sql)
