import openpyxl
from openpyxl.styles import Font, Alignment
from django.http import HttpResponse
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404
from AuthFit.models import Enrollment
from Gym.models import Gym

@login_required
def export_enrollments_excel(request, gym_id):
    gym = get_object_or_404(Gym, id=gym_id, owner=request.user)

    enrollments = (
        Enrollment.objects
        .filter(gym=gym, is_deleted=False)
        .order_by('fullname')
        .values_list('fullname', 'phone', 'unique_id')
    )

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Enrolled Members"

    ws.append(["Name", "Phone Number", "Unique ID"])
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center")

    for fullname, phone, unique_id in enrollments:
        ws.append([fullname, phone, unique_id])

    for col_cells in ws.columns:
        length = max(len(str(c.value)) if c.value else 0 for c in col_cells)
        ws.column_dimensions[col_cells[0].column_letter].width = length + 4

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    filename = f"{gym.gym_name}_enrollments.xlsx".replace(" ", "_")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    wb.save(response)
    return response