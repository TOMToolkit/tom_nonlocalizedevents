"""A workflow step that does nothing: the example a TOM copies to add its own step.

To add a workflow step: copy this module and ``templates/tom_nonlocalizedevents/workflow_steps/stub.html`` into your
``custom_code`` app (``custom_code/workflow/steps/my_workflow_step.py``), route the view in your ``urls.py``
with a pattern that takes ``event_id``, and add an entry to ``NONLOCALIZEDEVENTS_WORKFLOW_STEPS``::

    {'label': 'My workflow step',
     'url_name': 'custom_code:my-workflow-step',
     'cell_text': 'custom_code.workflow.steps.my_workflow_step.cell_text'}
"""
from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView

from tom_nonlocalizedevents.event_summary.views import EventSummaryMixin
from tom_nonlocalizedevents.models import NonLocalizedEvent


class WorkflowStepStubView(LoginRequiredMixin, EventSummaryMixin, TemplateView):
    """The stub workflow step page: the event summary above a short description of the step mechanism."""
    template_name = 'tom_nonlocalizedevents/workflow_steps/stub.html'


def cell_text(event: NonLocalizedEvent) -> str:
    """The text of this workflow step's cell on the event list; it runs once per row, so keep it cheap."""
    return 'open'
