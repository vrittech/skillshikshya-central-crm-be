from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.leadreport import services


class ReportViewSet(viewsets.ViewSet):
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
        return Response(services.lead_overview_report(request))

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
        return Response(services.lead_trend_report(request))

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
        return Response(services.counselor_performance_report(request))

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

