from io import BytesIO

from openpyxl import load_workbook
import pytest

from ayurschedule.excel_io import build_hospital_template


@pytest.fixture
def hospital_workbook_factory():
    def create(edit=None):
        workbook = load_workbook(BytesIO(build_hospital_template()))
        rows = {
            "Equipment": [["Massage_Table"], ["Steam_Chamber"], ["Unused_Equipment"]],
            "Therapies": [
                ["MASSAGE", "Hospital Massage", "Purvakarma", 60, 30, "Vata", "Massage_Table", "STEAM"],
                ["STEAM", "Hospital Steam", "Purvakarma", 30, 30, "Vata", "Steam_Chamber", None],
            ],
            "Doctors": [["Hospital Doctor", "F", "MASSAGE,STEAM", "08:00", "18:00", 360, 1]],
            "Rooms": [["Hospital Massage Room", "Massage_Table", 1], ["Hospital Steam Room", "Steam_Chamber", 1]],
            "Bookings": [
                ["B100", "2027-02-03", "0007", "Hospital Patient", "F", "MASSAGE", 2, "Vata", None],
                ["B101", "2027-02-03", "0007", "Hospital Patient", "F", "STEAM", None, None, "B100"],
            ],
        }
        for name, records in rows.items():
            for row_number, record in enumerate(records, start=2):
                for column_number, value in enumerate(record, start=1):
                    workbook[name].cell(row_number, column_number, value)
        if edit:
            edit(workbook)
        output = BytesIO()
        workbook.save(output)
        workbook.close()
        return output.getvalue()
    return create
