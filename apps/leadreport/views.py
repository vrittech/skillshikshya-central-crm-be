from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.global_config.permissions import ReadRights, can
from apps.leadreport import services


def _no_money(request):
    return not can(request.user, 'lead.view_payment')


class ReportViewSet(ReadRights, viewsets.ViewSet):
    read_perm = 'lead.view_lead'
    read_perms = {
        'revenue': 'lead.view_payment',
        'enrollments': 'lead.view_payment',
        'who_owes': 'lead.view_payment',
        'due_in_month': 'lead.view_payment',
        'batch_profit': 'lead.view_payment',
    }
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def list(self, request):
        return Response({'available_reports': [
            'overview', 'leads-by-status', 'leads-by-source',
            'leads-by-branch', 'leads-by-campaign', 'leads-by-counselor',
            'lead-trend', 'revenue', 'enrollments', 'conversion',
            'counselor-performance', 'who-owes', 'due-in-month', 'batch-profit',
            'enquiry-funnel',
        ]})

    @action(detail=False, methods=['get'])
    def overview(self, request):
        data = services.lead_overview_report(request)
        if _no_money(request):
            for part, key in (('pipeline', 'expected_revenue'), ('enrollments', 'revenue_collected')):
                if isinstance(data.get(part), dict):
                    data[part][key] = None
        return Response(data)

    @action(detail=False, methods=['get'], url_path='leads-by-status')
    def leads_by_status(self, request):
        return Response(services.leads_by_status_report(request))

    @action(detail=False, methods=['get'], url_path='leads-by-source')
    def leads_by_source(self, request):
        return Response(services.leads_by_source_report(request))

    @action(detail=False, methods=['get'], url_path='leads-by-branch')
    def leads_by_branch(self, request):
        return Response(services.leads_by_branch_report(request))

    @action(detail=False, methods=['get'], url_path='leads-by-campaign')
    def leads_by_campaign(self, request):
        return Response(services.leads_by_campaign_report(request))

    @action(detail=False, methods=['get'], url_path='leads-by-counselor')
    def leads_by_counselor(self, request):
        return Response(services.leads_by_counselor_report(request))

    @action(detail=False, methods=['get'], url_path='lead-trend')
    def lead_trend(self, request):
        data = services.lead_trend_report(request)
        if _no_money(request):
            for row in data.get('results', []):
                row['revenue'] = None
        return Response(data)

    @action(detail=False, methods=['get'])
    def revenue(self, request):
        return Response(services.revenue_report(request))

    @action(detail=False, methods=['get'])
    def enrollments(self, request):
        return Response(services.enrollment_report(request))

    @action(detail=False, methods=['get'])
    def conversion(self, request):
        return Response(services.lead_conversion_report(request))

    @action(detail=False, methods=['get'], url_path='counselor-performance')
    def counselor_performance(self, request):
        data = services.counselor_performance_report(request)
        if _no_money(request):
            rows = data.get('results', [])
            for row in rows:
                row['revenue_collected'] = None
            # Sorting by money would still show who collected most.
            if request.query_params.get('sort_by') == 'revenue_collected':
                rows.sort(key=lambda row: row['leads_assigned'], reverse=True)
        return Response(data)

    @action(detail=False, methods=['get'], url_path='who-owes')
    def who_owes(self, request):
        return Response(services.who_owes_report(request))

    @action(detail=False, methods=['get'], url_path='due-in-month')
    def due_in_month(self, request):
        return Response(services.due_in_month_report(request))

    @action(detail=False, methods=['get'], url_path='batch-profit')
    def batch_profit(self, request):
        return Response(services.batch_profit_report(request))

    @action(detail=False, methods=['get'], url_path='enquiry-funnel')
    def enquiry_funnel(self, request):
        return Response(services.enquiry_funnel_report(request))

