from __future__ import annotations

from reactors_czlab.sql.operations import get_data, row_to_csv

if __name__ == "__main__":
    all_rows = get_data("test", (20, "m"))
    row_to_csv("test.csv", all_rows)
