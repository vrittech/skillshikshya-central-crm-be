
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class CustomPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 100

    def get_paginated_response(self, data):
        return Response(
            {
                'count': self.page.paginator.count,
                'page_size': self.get_page_size(self.request),
                'max_page_size': self.max_page_size,
                'total_pages': self.page.paginator.num_pages,
                'current_page': self.page.number,
                'next': self.get_next_link(),
                'previous': self.get_previous_link(),
                'results': data,
            }
        )


class CallsPagination(CustomPagination):
    page_size = 20
