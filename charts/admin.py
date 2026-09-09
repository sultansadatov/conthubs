"""Admin for the charts app.

``ChartEntry`` is intentionally **not** registered with the Django admin: it uses
a composite primary key (which ``django.contrib.admin`` refuses to register) and
it is a range-partitioned, high-volume history table that should be inspected
through the Chart API, the ``charts_summary`` management command, or SQL — not
paged through row-by-row in the admin.
"""
