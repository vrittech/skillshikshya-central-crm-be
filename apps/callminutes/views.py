from django.shortcuts import get_object_or_404

from django.db.models import Prefetch

from rest_framework import generics, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.filters import SearchFilter, OrderingFilter
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend

from apps.account.models import User
from apps.callminutes.models import CallMinutesTransaction
from apps.callminutes.serializers import (
    CallMinutesTransactionSerializer, UserCallBalanceSerializer,
)
from apps.callminutes.services import add_minutes
from apps.global_config.pagination import CustomPagination
from apps.global_config.permissions import OPEN, ReadRights, STAFF, reads

DEFAULT_NESTED_LIMIT = 10
MAX_NESTED_LIMIT = 50


@api_view(['POST'])
@permission_classes([IsAdminUser])
def add_call_minutes(request, user_id):
    target = get_object_or_404(User, pk=user_id)
    amount = request.data.get('amount')
    note = request.data.get('note', '')
    target, txn = add_minutes(
        user_id=target.id,
        amount_minutes=amount,
        note=note,
        created_by=request.user,
    )
    return Response({
        'detail': f'Added {txn.amount // 60} call minutes to {target.email}.',
        'user': {'id': target.id, 'email': target.email},
        'balance_minutes': target.call_seconds_balance // 60,
        'balance_seconds': target.call_seconds_balance,
        'transaction': CallMinutesTransactionSerializer(txn).data,
    }, status=status.HTTP_200_OK)


class CallMinutesTransactionListView(ReadRights, generics.ListAPIView):
    read_perm = STAFF
    serializer_class = CallMinutesTransactionSerializer
    pagination_class = CustomPagination
    permission_classes = [IsAdminUser]

    def get_queryset(self):
        return (
            CallMinutesTransaction.objects
            .filter(user_id=self.kwargs['user_id'])
            .select_related('user', 'created_by', 'call')
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated, reads(OPEN)])
def my_call_minutes(request):
    recent = CallMinutesTransaction.objects.filter(user=request.user)[:10]
    balance = request.user.call_seconds_balance
    return Response({
        'balance_seconds': balance,
        'balance_minutes': balance // 60,
        'balance_display': f'{balance // 60}:{str(balance % 60).zfill(2)}',
        'recent_transactions': CallMinutesTransactionSerializer(recent, many=True).data,
    })


class CallBalanceListView(ReadRights, generics.ListAPIView):
    """Staff-only: every active user's call balance with recent activity."""
    read_perm = STAFF
    serializer_class = UserCallBalanceSerializer
    pagination_class = CustomPagination
    permission_classes = [IsAdminUser]
    filter_backends = [SearchFilter, OrderingFilter]
    search_fields = ['name', 'email']
    ordering_fields = ['name', 'email', 'call_seconds_balance']
    ordering = ['name']

    def _nested_limit(self, param):
        try:
            return max(1, min(MAX_NESTED_LIMIT, int(self.request.query_params.get(param, DEFAULT_NESTED_LIMIT))))
        except (TypeError, ValueError):
            return DEFAULT_NESTED_LIMIT

    def get_queryset(self):
        transactions_limit = self._nested_limit('transactions_limit')
        topups_limit = self._nested_limit('topups_limit')

        recent_txns = CallMinutesTransaction.objects.order_by('-created_at')
        return (
            User.objects.filter(is_active=True)
            .prefetch_related(
                Prefetch(
                    'call_minutes_transactions',
                    queryset=recent_txns[:transactions_limit],
                    to_attr='_recent_transactions',
                ),
                Prefetch(
                    'call_minutes_transactions',
                    queryset=recent_txns.filter(type='topup')[:topups_limit],
                    to_attr='_recent_topups',
                ),
            )
        )