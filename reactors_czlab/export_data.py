from __future__ import annotations

from reactors_czlab.sql.operations import query_data, row_to_csv

if __name__ == "__main__":
    all_rows = query_data((24, "m"))
    print(all_rows)
    row_to_csv("test.csv", all_rows)
