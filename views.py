from datetime import datetime

from django.conf import settings
from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.geos import Point
from django.contrib.gis.measure import D
from django.contrib.postgres.search import (
    SearchQuery,
    SearchVector,
    TrigramSimilarity,
)
from django.db.models import Q
from django.db.models.functions import Greatest
from django.http import Http404
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.cache import (
    cache_page,
    never_cache,
    patch_cache_control,
)
# from django.views.decorators.csrf import csrf_exempt
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import permissions, status, views, viewsets
from rest_framework.decorators import action, api_view
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from sendy.exceptions import SendyError

from core.filters import CategoryLimitFilter
from core.models import (
    Ad,
    Blog,
    Category,
    CategorySection,
    City,
    Event,
    EventBucket,
    Invite,
    NativeCache,
    Neighbourhood,
    NeighbourhoodGuide,
    Section,
    State,
    Subscription,
    UserMigration,
    Venue,
)
from core.pagination import DetailedPagination
from core.tasks import (
    cache_date_tab_city_page,
    cache_interests_city_page,
    cache_mobile_discover,
    cache_prices_tab_city_page,
    cache_state_page,
    cache_top_tab_city_page,
    do_pwa_curated_native_cache,
    send_invite_email,
)
from core.utils import (
    get_tampa,
    is_happening_now,
    is_refresh_cache,
    sendy,
    simple_texting,
    to_city_time,
)
from core.v2.filters import (
    AdFilter,
    BlogFilter,
    EventFilter,
    EventSearchFilter,
    ExperienceFilter,
    MapEventFilter,
    NeighbourhoodFilter,
    NeighbourhoodGuideFilter,
    SectionFilter,
    StateSectionFilter,
    VenueFilter,
)
from core.v2.pagination import (
    CitySearchPagination,
    EventBucketPagination,
    EventCategoryPagination,
    EventPagination,
    MapPagination,
    SectionPagination,
)
from core.v2.serializers import (
    AdSerializer,
    BasicEventSerializer,
    BlogLightSerializer,
    BlogSerializer,
    CategoryEventsSerializer,
    CategorySerializer,
    CategoryTreeSerializer,
    CitySearchSerializer,
    CitySerializer,
    DesktopSectionSerializer,
    EventBucketSerializer,
    EventDetailSerializer,
    EventSearchSerializer,
    EventSerializer,
    GuestListSerializer,
    LightBasicEventSerializer,
    LightCitySerializer,
    LightDesktopSectionSerializer,
    LightEventSerializer,
    LocationSerializer,
    NeighbourhoodDetailSerializer,
    NeighbourhoodGuideSerializer,
    NeighbourhoodLightSerializer,
    NeighbourhoodSerializer,
    PartnerSerializer,
    SectionLightSerializer,
    SectionSerializer,
    StateSectionLightSerializer,
    StateSectionSerializer,
    StateSerializer,
    SubscriptionSerializer,
    ThemeSerializer,
    UserInviteSerializer,
    UserSerializer,
    VenueLightSerializer,
    VenueSerializer,
)
from core.v2.validators import RequiredFieldValidation
from core.v2.views.mixins import AddEventActionMixin
from core.views import views as core_views
from core.views.views import RelatedEventMixin, get_version, trigger_error
from desktop.utils import get_important_cities_slug
from experiences.models import Experience
from experiences.serializers import ExperienceLightSerializer
from orbweaver.mongomodels import User as MongoUser

__all__ = [
    'BlogUpdate',
    'BlogViewSet',
    'CategoryViewSet',
    'CityViewSet',
    'RelatedEventMixin',
    'EventDetailViewSet',
    'EventOldDetailViewSet',
    'EventDetailRelatedViewSet',
    'GuestListViewSet',
    'LocationViewSet',
    'MigrateEventView',
    'NeighbourhoodViewSet',
    'PartnerViewSet',
    'SubscriptionViewSet',
    'ThemeViewSet',
    'UserViewSet',
    'VenueViewSet',
    'get_version',
    'trigger_error',
]


class NeighbourhoodViewSet(AddEventActionMixin, viewsets.ModelViewSet):
    queryset = Neighbourhood.objects.all().order_by('-created_at')
    serializer_class = NeighbourhoodSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend]
    filterset_class = NeighbourhoodFilter
    lookup_field = 'slug'

    def get_serializer_class(self, *args, **kwargs):
        if self.action == 'retrieve':
            return NeighbourhoodDetailSerializer
        else:
            return super().get_serializer_class(*args, **kwargs)


class EventViewSet(RelatedEventMixin, viewsets.ModelViewSet):
    serializer_class = EventSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend]
    filterset_class = EventFilter
    pagination_class = EventPagination

    def get_serializer_class(self, *args, **kwargs):
        if self.action == 'retrieve':
            return EventDetailSerializer
        elif self.action == 'light':
            return LightBasicEventSerializer
        elif self.action == 'basic':
            return BasicEventSerializer
        elif self.action == 'list':
            return LightBasicEventSerializer
        else:
            return super().get_serializer_class(*args, **kwargs)

    def get_nearest_curated_city(self, city, radius):

        if radius == EventFilter.FIVE_MILE:
            distance = 5
        elif radius == EventFilter.TWENTY_FIVE_MILE:
            distance = 25
        elif radius == EventFilter.FIFTY_MILE:
            distance = 50
        elif radius == EventFilter.HUNDRED_MILE:
            distance = 100
        elif radius == EventFilter.HUNDRED_PLUS_MILE:
            distance = 10000
        else:
            return False

        point = Point(y=city.point.y, x=city.point.x, srid=4326)
        cities = City.objects.filter(
            is_curated=True,
            point__dwithin=(point, D(mi=distance))
        )
        cities = cities.annotate(
            distance=Distance("point", point)
        ).order_by('distance')

        if not cities and city.slug in get_tampa():
            cities = City.objects.filter(
                slug='tampa-bay-florida-united-states'
            )

        return cities

    def get_queryset(self):
        query_params = self.request.query_params
        radius = query_params.get('radius', EventFilter.HUNDRED_PLUS_MILE)
        query_params._mutable = True

        self.kwargs['radius'] = radius
        query_params.update({
            'radius': radius,
        })

        query_params._mutable = False

        if self.is_search():
            return self.search_events()

        queryset = self.queryset = Event.objects
        is_past_events = query_params.get('when', None)
        what = query_params.get('what', None)
        self.queryset = queryset.viewable().select_related('theme', 'user')
        if is_past_events and is_past_events == EventFilter.PAST_EVENTS:
            self.queryset = queryset.viewable(
                past_events=True
            ).select_related('theme', 'user').order_by('-end_date')

        city_slug = query_params.get('city')
        if city_slug:
            try:
                city = City.objects.get(slug=city_slug)
            except City.DoesNotExist:
                is_curated = False
                city = None
            else:
                is_curated = city.is_curated

            if not is_curated:
                qs = self.queryset
                if city:
                    curated_city = self.get_nearest_curated_city(city, radius)
                    if not curated_city:
                        qs = qs.filter(is_featured=False)
                        qs = qs.filter(is_staff_picked=False)
                    else:
                        nearest_city = curated_city.first().id
                        curated_city = curated_city.exclude(id=nearest_city)
                        qs = qs.exclude(
                            Q(is_featured=True) |
                            Q(is_staff_picked=True),
                            locations__city__in=curated_city,
                        )

                    self.queryset = qs
        if what:
            self.queryset = queryset.filter(start_date__gte=timezone.now())

        return self.queryset.order_by('start_date')

    @action(detail=False)
    def curated(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        queryset = queryset.filter(is_staff_picked=True)
        queryset = self.filter_queryset(queryset)

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=False)
    def prices(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        queryset = self.filter_queryset(queryset)

        free = queryset.filter(
            prices_available__contains=[EventFilter.FREE],
        )

        price_10 = queryset.filter(
            prices_available__contains=[EventFilter.PRICE_10],
        )

        price_25 = queryset.filter(
            prices_available__contains=[EventFilter.PRICE_25],
        )

        price_50 = queryset.filter(
            prices_available__contains=[EventFilter.PRICE_50],
        )

        price_100 = queryset.filter(
            prices_available__contains=[EventFilter.PRICE_100],
        )

        price_100_plus = queryset.filter(
            prices_available__contains=[EventFilter.PRICE_100_plus]
        )

        def get_events(qs):
            return qs.exclude(locations=None).order_by('-start_date')[:10]

        def exclude_ongoing_events(qs):
            utc_now = timezone.now()
            return qs.exclude(
                start_date__lte=utc_now,
                end_date__gte=utc_now,
            )

        data = {
            "results": [
                {"name": EventFilter.FREE,
                 'objects': BasicEventSerializer(
                     get_events(exclude_ongoing_events(free)),
                     many=True,
                     context={'request': request},
                 ).data
                 },
                {"name": EventFilter.PRICE_10,
                 'objects': BasicEventSerializer(
                     get_events(price_10),
                     many=True,
                     context={'request': request},
                 ).data},
                {"name": EventFilter.PRICE_25,
                 'objects': BasicEventSerializer(
                     get_events(price_25),
                     many=True,
                     context={'request': request},
                 ).data},
                {"name": EventFilter.PRICE_50,
                 'objects': BasicEventSerializer(
                     get_events(price_50),
                     many=True,
                     context={'request': request},
                 ).data},
                {"name": EventFilter.PRICE_100,
                 'objects': BasicEventSerializer(
                     get_events(price_100),
                     many=True,
                     context={'request': request},
                 ).data},
                {"name": EventFilter.PRICE_100_plus,
                 'objects': BasicEventSerializer(
                     get_events(price_100_plus),
                     many=True,
                     context={'request': request},
                 ).data},
            ]
        }

        return Response(data)

    @action(detail=False)
    def categories(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        queryset = self.filter_queryset(queryset)
        category_sections = CategorySection.objects.all(
        ).order_by('category__name')

        def get_events(qs):
            return qs.exclude(locations=None)[:10]

        res = []
        for category_section in category_sections:
            events = queryset.filter(
                categories__id=category_section.category.id
            ).order_by('-start_date')
            res_item = {
                'name': category_section.category.name,
                'slug': category_section.category.slug,
                'objects': BasicEventSerializer(
                    get_events(events),
                    many=True,
                    context={'request': request},
                ).data
            }
            res.append(res_item)

        return Response({"results": res})

    @action(detail=False)
    def categories_paginated(self, request, *args, **kwargs):
        query_params = self.request.query_params

        city = query_params.get('city', None)
        radius = query_params.get('radius', None)
        page_no = query_params.get('page', 1)
        page_size = query_params.get(
                            'page_size', self.pagination_class.page_size)

        response = NativeCache.objects.filter(
            url='/v2/events/categories_paginated/',
            platform=Section.DESKTOP,
            page_type=Section.CITY_PAGE,
            tab=Section.INTERESTS_TAB,
            city=city,
            radius=radius,
            page_no=page_no,
            query_params=[page_size]
        )

        if response.exists():
            cache = response.first()
            if not is_refresh_cache(cache):
                return Response(cache.response)

        self.pagination_class = EventCategoryPagination
        categories = Category.objects.viewable()

        events = Event.objects.viewable(
            check_start_date=True
        )
        filterset = EventFilter(
            queryset=events,
            request=request,
            data=self.request.query_params,
        )
        ids = filterset.qs.values_list('id', flat=True)

        categories = categories.filter(
            events__in=ids
        ).distinct().order_by('name')

        page = self.paginate_queryset(categories)
        if page is not None:
            if response.exists():
                cache = response.first()

                if is_refresh_cache(cache):
                    qs = [obj.pk for obj in categories]
                    categories = [obj.pk for obj in page]
                    cache_interests_city_page.delay(
                        qs, categories, city, page_no, page_size, radius)

                return Response(cache.response)
            else:
                qs = [obj.pk for obj in categories]
                categories = [obj.pk for obj in page]
                cache_interests_city_page.delay(
                    qs, categories, city, page_no, page_size, radius)

            serializer = CategoryEventsSerializer(
                page,
                many=True,
                context={'request': request},
            )
            return self.get_paginated_response(serializer.data)

        serializer = CategoryEventsSerializer(
            categories,
            many=True,
            context={'request': request},
        )
        return Response(serializer.data)

    @action(detail=False)
    def when_paginated(self, request, *args, **kwargs):
        self.pagination_class = EventBucketPagination
        event_bucket = EventBucket.objects.filter(
            bucket_type=EventBucket.WHEN, is_active=True
            ).order_by('sort_order')

        query_params = self.request.query_params
        registered_user = query_params.get('registered_user', 'false')
        if registered_user.lower() == 'true':
            event_bucket = event_bucket.exclude(
                bucket=EventBucket.HAPPENING_LATER,
            )

        page = self.paginate_queryset(event_bucket)
        if page is not None:
            city = query_params.get('city', None)
            radius = query_params.get('radius', None)
            page_no = query_params.get('page', 1)
            page_size = query_params.get(
                'page_size', self.pagination_class.page_size)

            response = NativeCache.objects.filter(
                url='/v2/events/when_paginated/',
                platform=Section.DESKTOP,
                page_type=Section.CITY_PAGE,
                tab=Section.DATE_TAB,
                city=city,
                radius=radius,
                page_no=page_no,
                query_params=[page_size],
            )
            if response.exists():
                cache = response.first()

                if is_refresh_cache(cache):
                    buckets = [obj.pk for obj in page]
                    event_bucket = [obj.pk for obj in event_bucket]
                    cache_date_tab_city_page.delay(
                        event_bucket,
                        buckets, city,
                        page_no, page_size,
                        radius
                    )

                return Response(cache.response)
            else:
                buckets = [obj.pk for obj in page]
                event_bucket = [obj.pk for obj in event_bucket]

                cache_date_tab_city_page.delay(
                    event_bucket, buckets,
                    city, page_no,
                    page_size, radius
                )

            serializer = EventBucketSerializer(
                page,
                many=True,
                context={'request': request},
            )
            return self.get_paginated_response(serializer.data)

        serializer = EventBucketSerializer(
            event_bucket,
            many=True,
            context={'request': request},
        )
        return Response(serializer.data)

    @action(detail=False)
    def prices_paginated(self, request, *args, **kwargs):
        query_params = self.request.query_params
        self.pagination_class = EventBucketPagination
        event_bucket = EventBucket.objects.filter(
            bucket_type=EventBucket.PRICE, is_active=True
            ).order_by('sort_order')
        page = self.paginate_queryset(event_bucket)
        if page is not None:
            city = query_params.get('city', None)
            radius = query_params.get('radius', None)
            page_no = query_params.get('page', 1)
            page_size = query_params.get(
                'page_size', self.pagination_class.page_size)

            response = NativeCache.objects.filter(
                url='/v2/events/prices_paginated/',
                platform=Section.DESKTOP,
                page_type=Section.CITY_PAGE,
                tab=Section.PRICE_TAB,
                city=city,
                radius=radius,
                page_no=page_no,
                query_params=[page_size],
            )
            if response.exists():
                cache = response.first()

                if is_refresh_cache(cache):
                    buckets = [obj.pk for obj in page]
                    event_bucket = [obj.pk for obj in event_bucket]
                    cache_prices_tab_city_page.delay(
                        event_bucket, buckets,
                        city, page_no, page_size,
                        radius)

                return Response(cache.response)
            else:
                buckets = [obj.pk for obj in page]
                event_bucket = [obj.pk for obj in event_bucket]
                cache_prices_tab_city_page.delay(
                    event_bucket, buckets, city, page_no, page_size, radius)

            serializer = EventBucketSerializer(
                page,
                many=True,
                context={'request': request},
            )
            return self.get_paginated_response(serializer.data)

        serializer = EventBucketSerializer(
            event_bucket,
            many=True,
            context={'request': request},
        )
        return Response(serializer.data)

    @action(detail=False)
    def when(self, request, *args, **kwargs):
        query_params = self.request.query_params
        queryset = self.get_queryset()
        query_params._mutable = True

        query_params.update({
            'when': EventFilter.NOW
        })
        now = self.filter_queryset(queryset)
        del query_params["when"]
        query_params.update({
            'when': EventFilter.TODAY
        })
        today = self.filter_queryset(queryset)
        del query_params["when"]
        query_params.update({
            'when': EventFilter.TOMORROW
        })
        tomorrow = self.filter_queryset(queryset)
        del query_params["when"]
        query_params.update({
            'when': EventFilter.THIS_WEEK
        })
        this_week = self.filter_queryset(queryset)
        del query_params["when"]
        query_params.update({
            'when': EventFilter.WEEKEND
        })
        weekend = self.filter_queryset(queryset)
        del query_params["when"]
        query_params.update({
            'when': EventFilter.NEXT_WEEK
        })
        next_week = self.filter_queryset(queryset)
        del query_params["when"]
        query_params.update({
            'when': EventFilter.NEXT_WEEKEND
        })
        next_weekend = self.filter_queryset(queryset)
        registered_user = query_params.get('registered_user', 'false')

        if registered_user.lower() == 'false':
            del query_params["when"]
            query_params.update({
                'when': EventFilter.HAPPENING_LATER
            })
            happening_later = self.filter_queryset(
                queryset).order_by('-start_date')

        del query_params["when"]
        query_params.update({
            'when': EventFilter.PAST_EVENTS
        })
        past_events = self.filter_queryset(
            Event.objects.viewable(
                past_events=True).select_related('theme', 'user')
        ).order_by('-end_date')

        query_params._mutable = False

        def get_events(qs):
            return qs.exclude(locations=None).order_by('-start_date')[:10]

        res = {
            "results": [
                {'name': EventFilter.NOW,
                 'objects':
                    BasicEventSerializer(
                        get_events(now),
                        many=True,
                        context={'request': request},
                    ).data},
                {'name': EventFilter.TODAY,
                 'objects':
                    BasicEventSerializer(
                        get_events(today),
                        many=True,
                        context={'request': request},
                    ).data},
                {'name': EventFilter.TOMORROW,
                 'objects':
                    BasicEventSerializer(
                        get_events(tomorrow),
                        many=True,
                        context={'request': request},
                    ).data},
                {'name': EventFilter.THIS_WEEK,
                 'objects':
                    BasicEventSerializer(
                        get_events(this_week),
                        many=True,
                        context={'request': request},
                    ).data},
                {'name': EventFilter.WEEKEND,
                 'objects':
                    BasicEventSerializer(
                        get_events(weekend),
                        many=True,
                        context={'request': request},
                    ).data},
                {'name': EventFilter.NEXT_WEEK,
                 'objects':
                    BasicEventSerializer(
                        get_events(next_week),
                        many=True,
                        context={'request': request},
                    ).data},
                {'name': EventFilter.NEXT_WEEKEND,
                 'objects':
                    BasicEventSerializer(
                        get_events(next_weekend),
                        many=True,
                        context={'request': request},
                    ).data},
            ]
        }

        if registered_user.lower() == 'false':
            res['results'].append(
                {'name': EventFilter.HAPPENING_LATER,
                 'objects':
                    BasicEventSerializer(
                        get_events(happening_later),
                        many=True,
                        context={'request': request},
                    ).data},
            )
        res['results'].append(
            {'name': EventFilter.PAST_EVENTS,
             'objects':
                BasicEventSerializer(
                    get_events(past_events),
                    many=True,
                    context={'request': request},
                ).data},
        )

        return Response(res)

    @action(detail=False)
    def count(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        queryset = self.filter_queryset(queryset)
        count = queryset.count()
        return Response(dict(count=count))

    @action(detail=True)
    def related(self, *args, **kwargs):
        return self.get_related(*args, **kwargs)

    @action(detail=False, methods=["GET"])
    def light(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @action(detail=False)
    def basic(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def is_search(self):
        value = self.request.query_params.get('search', '')
        if value:
            return True

        return False

    def search_events(self):
        query_params = self.request.query_params
        is_past_events = query_params.get('when', None)
        events = Event.objects.viewable().select_related('theme')
        if is_past_events and is_past_events == EventFilter.PAST_EVENTS:
            events = Event.objects.viewable(
                past_events=True
            ).select_related('theme', 'user')
        events = self.filter_search(events).distinct()
        return events

    def filter_search(self, queryset):
        value = self.request.query_params.get('search', '')
        if len(value) < 3:
            return queryset.none()

        query = SearchQuery(
            value,
            config='english',
            search_type='phrase',
        )

        ft_search = queryset.annotate(search=SearchVector(
            'name',
            'description',
            config='english',
        )).filter(
            Q(search=query) |
            Q(name__istartswith=value)
        )

        if ft_search.exists():
            return ft_search

        name_search = queryset.filter(name__istartswith=value)
        if name_search.exists():
            return name_search

        ts_search = queryset.annotate(similarity=Greatest(
            TrigramSimilarity('name', value),
            TrigramSimilarity('description', value),
        )).filter(similarity__gt=0.03).order_by('-similarity')

        if ts_search.exists():
            return ts_search

        general = queryset.filter(
            Q(name__icontains=value)
            | Q(description__icontains=value)
        )

        return general

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        key = self.request.query_params.get('search')
        if key:
            response.data.update({"search_key": key})

        return response


class NeighbourhoodGuideViewSet(AddEventActionMixin, viewsets.ModelViewSet):
    queryset = NeighbourhoodGuide.objects.all().order_by('-created_at')
    serializer_class = NeighbourhoodGuideSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend]
    filterset_class = NeighbourhoodGuideFilter
    lookup_field = 'slug'


class SectionViewSet(viewsets.ModelViewSet):
    queryset = Section.objects.viewable().order_by('sort_order')
    serializer_class = SectionSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend]
    filterset_class = SectionFilter
    pagination_class = SectionPagination
    lookup_field = 'slug'

    def get_extra_fields_to_show(self):
        return ['name', 'slug', 'description', 'section_class']

    def filter_queryset(self, queryset):
        if queryset.model is Section:
            query_params = self.request.query_params
            platform = query_params.get('platform', None)
            if self.action == 'mobile':
                included = [
                    Section.TOP_EVENTS,
                    Section.CURATED_BY_LOCALS,
                    Section.UPCOMING_EVENTS,
                ]
                queryset = queryset.filter(section_class__in=included).exclude(
                    platform__in=[Section.DESKTOP, Section.MOBILE])
            elif not platform:
                if self.action == 'retrieve':
                    return super().filter_queryset(queryset)

                excluded = [
                    Section.NEIGHBOURHOODS,
                    Section.NEIGHBOURHOOD_GUIDE,
                    Section.EXPLORE_CITY_GUIDES,
                    Section.PROMOTE_EVENT,
                    Section.SELL_TICKET,
                    Section.FOLLOW_US,
                    Section.STAY_UPDATED,
                    Section.DOWNLOAD_APP,
                    Section.DISCOVER_EVENT,
                    Section.DISCOVER_EVENT_OTHER_CITIES,
                    Section.CREATE_EVENT,
                    Section.STATE_SECTION,
                ]

                queryset = queryset.exclude(
                    Q(section_class__in=excluded) |
                    Q(platform__in=[Section.DESKTOP, Section.MOBILE])
                )

            elif platform == Section.DESKTOP:
                page_type = query_params.get('page_type', None)

                if page_type == Section.CTA_SEE_ALL:
                    city_slug = query_params.get('city', None)
                    try:
                        city = City.objects.get(slug=city_slug)
                    except City.DoesNotExist:
                        queryset = queryset.exclude(
                            Q(is_curated=True) |
                            Q(section_class=Section.EXPLORE_MORE),
                        )

                elif page_type == Section.EVENT_DETAIL_PAGE:
                    city_slug = query_params.get('city', None)

                    try:
                        city = City.objects.get(slug=city_slug)
                        if not city.is_curated:
                            queryset = queryset.exclude(
                                Q(is_curated=True) |
                                Q(section_class=Section.FEATURED_CITY_GUIDES) |
                                Q(section_class=Section.TOP_EVENTS)
                            )
                    except City.DoesNotExist:
                        queryset = queryset.exclude(
                            Q(is_curated=True) |
                            Q(section_class=Section.FEATURED_CITY_GUIDES) |
                            Q(section_class=Section.TOP_EVENTS)
                        )

        return super().filter_queryset(queryset)

    def get_response(self, queryset):
        queryset = self.filter_queryset(queryset)

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    def get_serializer_class(self, *args, **kwargs):
        query_params = self.request.query_params
        platform = query_params.get('platform', None)
        if self.action == 'desktop':
            return LightDesktopSectionSerializer
        if platform == Section.DESKTOP:
            return DesktopSectionSerializer
        if self.action == 'list':
            return SectionLightSerializer
        else:
            return super().get_serializer_class(*args, **kwargs)

    def get_object(self):
        city_slug = self.request.query_params.get('city')
        if not city_slug:
            return super().get_object()

        section_slug = self.kwargs.get('slug')
        city = get_object_or_404(City.objects, slug=city_slug)
        if not city.is_curated:
            if section_slug.endswith(city_slug):
                max_length = len(section_slug) - len(city_slug)
                section_slug = section_slug[:max_length-1]
                self.kwargs['slug'] = section_slug

        return super().get_object()

    @method_decorator(cache_page(60*5))
    def list(self, request, *args, **kwargs):
        query_params = request.query_params
        url = '/v2/sections/'
        city = query_params.get('city', None)
        radius = query_params.get('radius', None)
        cache_refresh = query_params.get('cache_refresh', None)

        if not cache_refresh and city:
            response = NativeCache.objects.filter(
                url=url,
                platform=Section.PWA,
                page_type=Section.CITY_PAGE,
                tab=Section.TOP_TAB,
                city=city,
                radius=radius,
            )

            if response.exists():
                cache = response.first()
                if not is_refresh_cache(cache):
                    return Response(cache.response)
                else:
                    do_pwa_curated_native_cache.delay(city, radius)
                    return Response(cache.response)
            else:
                do_pwa_curated_native_cache.delay(city, radius)

        return super().list(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        self._object = section = self.get_object()

        self.pagination_class = DetailedPagination

        if section.section_type == section.EVENT_TYPE:
            params = self.request.query_params

            radius = params.get('radius')
            is_100_plus = radius == EventFilter.HUNDRED_PLUS_MILE
            check_start_date = not is_happening_now(params)

            queryset = section.get_events(
                is_100_plus=is_100_plus,
                check_start_date=check_start_date,
            ).order_by('start_date')

            self.serializer_class = LightBasicEventSerializer
            self.filterset_class = EventFilter

        elif section.section_type == section.CATEGORY_TYPE:
            queryset = section.categories.all().order_by('-created_at')
            self.serializer_class = CategorySerializer
            self.filterset_class = None

        elif section.section_type == section.VENUE_TYPE:
            queryset = Venue.objects.all()
            queryset = queryset.exclude(sections__events=None)
            self.serializer_class = VenueLightSerializer
            self.filterset_class = VenueFilter

        elif section.section_type == section.NEIGHBOURHOOD_TYPE:
            queryset = section.neighbourhoods.all().order_by('-created_at')
            self.serializer_class = NeighbourhoodLightSerializer
            self.filterset_class = NeighbourhoodFilter

        elif section.section_type == section.GUIDE_TYPE:
            queryset = section.blogs.filter(
                is_stuff_to_do=True)
            self.serializer_class = BlogLightSerializer
            self.filterset_class = BlogFilter

        elif section.section_type == section.EXPERIENCE_TYPE:
            queryset = Experience.objects.all().order_by('-created_at')
            self.serializer_class = ExperienceLightSerializer
            self.filterset_class = ExperienceFilter

        elif section.section_type == section.NEIGHBOURHOOD_GUIDE:
            queryset = NeighbourhoodGuide.objects.all().order_by('-created_at')
            self.serializer_class = NeighbourhoodGuideSerializer
            self.filterset_class = NeighbourhoodGuideFilter

        elif section.section_type == section.ADS_TYPE:
            queryset = Ad.objects.all().order_by('-created_at')
            self.serializer_class = AdSerializer
            self.filterset_class = AdFilter

        self.queryset = queryset
        response = self.get_response(queryset)
        patch_cache_control(response, public=True)
        return response

    @action(detail=False)
    def mobile(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        queryset = self.filter_queryset(queryset)
        query_params = request.query_params

        city = query_params.get('city', None)
        radius = query_params.get('radius', None)

        response = NativeCache.objects.filter(
            url='/v2/sections/mobile/',
            platform=Section.MOBILE,
            city=city,
            radius=radius,
        )
        if response.exists():
            cache = response.first()

            if is_refresh_cache(cache):
                sections = [obj.pk for obj in queryset]
                cache_mobile_discover.delay(sections, city, radius)

            return Response(cache.response)
        else:
            sections = [obj.pk for obj in queryset]
            cache_mobile_discover.delay(sections, city, radius)

        return self.get_response(queryset)

    @action(detail=False)
    def desktop(self, request, *args, **kwargs):
        query_params = request.query_params
        url = '/v2/sections/desktop/'
        platform = query_params.get('platform', None)
        page_type = query_params.get('page_type', None)
        tab = query_params.get('tab', None)
        city = query_params.get('city', None)
        radius = query_params.get('radius', None)
        cache_refresh = query_params.get('cache_refresh', None)

        if not cache_refresh:
            response = NativeCache.objects.filter(
                url=url,
                platform=platform,
                page_type=page_type,
                tab=tab,
                city=city,
                radius=radius,
            )

            if response.exists():
                cache = response.first()
                if not is_refresh_cache(cache):
                    return Response(cache.response)
                else:
                    cache_top_tab_city_page.delay(
                        url, platform, page_type, tab, city, radius)
                    return Response(cache.response)

        queryset = self.get_queryset()
        queryset = self.filter_queryset(queryset)

        return self.get_response(queryset)


class StateSectionViewSet(SectionViewSet):
    queryset = Section.objects.viewable().order_by('sort_order')
    serializer_class = StateSectionSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend]
    filterset_class = StateSectionFilter
    lookup_field = 'slug'

    def get_extra_fields_to_show(self):
        return ['name', 'slug', 'description', 'section_class']

    def get_serializer_class(self, *args, **kwargs):
        if self.action == 'list':
            return StateSectionLightSerializer
        else:
            return super().get_serializer_class(*args, **kwargs)

    def filter_queryset(self, queryset):
        if queryset.model is Section:
            queryset = queryset.filter(platform=Section.DESKTOP)

        return super().filter_queryset(queryset)

    def list(self, request, *args, **kwargs):
        query_params = request.query_params

        url = '/v2/state/sections/'
        platform = query_params.get('platform', None)
        page_type = query_params.get('page_type', None)
        state = query_params.get('state', None)
        cache_refresh = query_params.get('cache_refresh', None)
        if not cache_refresh:
            response = NativeCache.objects.filter(
                url=url,
                platform=platform,
                page_type=page_type,
                state__iexact=state,
            )
            if response.exists():
                cache = response.first()
                if not is_refresh_cache(cache):
                    return Response(cache.response)
            cache_state_page.delay(
                url, platform, page_type, state)

        return super().list(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        self._object = section = self.get_object()

        self.pagination_class = DetailedPagination

        if section.section_type == section.EVENT_TYPE:
            params = self.request.query_params

            radius = params.get('radius')
            is_100_plus = radius == EventFilter.HUNDRED_PLUS_MILE
            check_start_date = not is_happening_now(params)

            queryset = section.get_events(
                is_100_plus=is_100_plus,
                check_start_date=check_start_date,
            )

            self.serializer_class = LightBasicEventSerializer
            self.filterset_class = EventFilter

            if section.section_class == section.DISCOVER_EVENT:
                queryset = City.objects.filter(
                    slug__in=get_important_cities_slug(),
                )
                self.serializer_class = LightCitySerializer
                self.filterset_class = None

        elif section.section_type == section.GUIDE_TYPE:
            queryset = section.blogs.all()
            self.serializer_class = BlogLightSerializer
            self.filterset_class = BlogFilter

        elif section.section_type == section.STATE_TYPE:
            params = self.request.query_params
            radius = params.get('state')
            queryset = State.objects.all()
            self.serializer_class = StateSerializer
            self.filterset_class = None

        self.queryset = queryset
        return self.get_response(queryset)


class AdViewSet(viewsets.ModelViewSet):
    queryset = Ad.objects.all().order_by('-created_at')
    serializer_class = AdSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        city_slug = self.request.query_params.get("city", None)

        try:
            city = City.objects.get(slug=city_slug)
        except City.DoesNotExist:
            return self.queryset.none()

        queryset = self.queryset.filter(city__slug=city_slug, is_internal=True)

        utc_time = to_city_time(city, timezone.now())
        queryset = queryset.filter(
            start_date__lte=utc_time,
            end_date__gte=utc_time,
        )[:3]
        if not queryset:
            queryset = self.queryset.filter(
                city__slug=city, is_internal=False)[:1]

        queryset = self.filter_queryset(queryset)

        return queryset


class SubscriptionViewSet(viewsets.ModelViewSet):
    queryset = Subscription.objects.all().order_by('created_at')
    serializer_class = SubscriptionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self, subscription_id):
        try:
            return Subscription.objects.get(id=subscription_id)
        except Subscription.DoesNotExist:
            raise Http404

    @action(detail=False, methods=["POST"])
    def unsubscribe(self, request, *args, **kwargs):
        subscription_id = request.data.get('pk')
        subscription = self.get_object(subscription_id)
        city = subscription.city
        if subscription.email:
            sendy.remove_email_from_list(
                subscription.email, unsubscribe_list=city.sendy_list)

        if subscription.email and subscription.phone_number:
            simple_texting.remove_phone_from_list(
                subscription.phone_number,
                group=city.simple_texting_group
            )
        subscription.delete()
        return Response({
            "success": True,
            "message": "Unsubscribe successfully"
        })

    def create(self, request):
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            response = dict()
            response["status"] = 400
            response["error"] = serializer._errors
            return Response(response, status=400)

        validated_data = serializer.validated_data
        email = validated_data.get('email')
        city = validated_data.get('city')
        response = dict()
        response["error"] = False
        response["already"] = False
        if email and settings.SENDY_URL:
            try:
                if city:
                    sendy.add_email_to_list(email, city.sendy_list)
                else:
                    sendy.add_email_to_list(email)
            except SendyError as e:
                if "Already subscribed" not in str(e):
                    response = {"sendy": str(e),
                                "error": True,
                                "already": False
                                }
                if "Already subscribed" in str(e):
                    response = {"sendy": str(e),
                                'already': True,
                                "error": False}

        phone_number = validated_data.get('phone_number')
        if phone_number:
            if city:
                result = simple_texting.add_phone_to_list(
                    phone_number,
                    group=city.simple_texting_group
                )
            else:
                result = simple_texting.add_phone_to_list(phone_number)
            if result["code"] not in [1, -607, -610]:
                response["simple_texting"] = result
                response["error"] = True
            else:
                response["simple_texting"] = result
                response["error"] = False
                response["already"] = True

        if response["error"]:
            response["status"] = 400
            return Response(response, status=400)
        elif response["already"]:
            response["status"] = 206
            return Response(response, status=206)
        else:
            serializer.save()
            response["status"] = 200
            return super().create(request)


class VenueViewSet(core_views.VenueViewSet):
    serializer_class = VenueSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = VenueFilter

    def retrieve(self, request, *args, **kwargs):
        venue = self.get_object()

        query_params = self.request.query_params
        query_params._mutable = True
        query_params.update({
            'venue': venue.slug
        })
        query_params._mutable = False

        self.pagination_class = EventPagination
        self.serializer_class = LightBasicEventSerializer
        self.filterset_class = EventFilter

        self.queryset = Event.objects.viewable(
        ).select_related('theme', 'user')

        return self.get_response(self.queryset)

    def get_response(self, queryset):
        queryset = self.filter_queryset(queryset)
        queryset = queryset.distinct().order_by('start_date')

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)


class MapViewSet(viewsets.ModelViewSet):
    serializer_class = BasicEventSerializer
    pagination_class = MapPagination
    filter_backends = [DjangoFilterBackend]
    filterset_class = MapEventFilter

    def get_queryset(self):
        query_params = self.request.query_params

        if self.is_search():
            return self.search_events()

        queryset = self.queryset = Event.objects
        is_past_events = query_params.get('when', None)
        self.queryset = queryset.viewable().select_related('theme', 'user')
        if is_past_events and is_past_events == EventFilter.PAST_EVENTS:
            self.queryset = queryset.viewable(
                past_events=True
            ).select_related('theme', 'user').order_by('-end_date')

        city_slug = query_params.get('city')
        if city_slug:
            try:
                city = City.objects.get(slug=city_slug)
            except City.DoesNotExist:
                is_curated = False
                city = None
            else:
                is_curated = city.is_curated

            if not is_curated:
                qs = self.queryset
                if city:
                    radius = query_params.get('radius', None)
                    curated_city = self.get_nearest_curated_city(city, radius)
                    if not curated_city:
                        qs = qs.filter(is_featured=False)
                        qs = qs.filter(is_staff_picked=False)
                    else:
                        nearest_city = curated_city.first().id
                        curated_city = curated_city.exclude(id=nearest_city)
                        qs = qs.exclude(
                            Q(is_featured=True) |
                            Q(is_staff_picked=True),
                            locations__city__in=curated_city,
                        )

                    self.queryset = qs

        return self.queryset

    def is_search(self):
        value = self.request.query_params.get('search', '')
        if value:
            return True

        return False

    def search_events(self):
        query_params = self.request.query_params
        is_past_events = query_params.get('when', None)
        events = Event.objects.viewable().select_related('theme')
        if is_past_events and is_past_events == EventFilter.PAST_EVENTS:
            events = Event.objects.viewable(
                past_events=True
            ).select_related('theme', 'user')
        events = self.filter_search(events).distinct()
        return events

    def filter_search(self, queryset):
        value = self.request.query_params.get('search', '')
        if len(value) < 3:
            return queryset.none()

        query = SearchQuery(
            value,
            config='english',
            search_type='phrase',
        )

        ft_search = queryset.annotate(search=SearchVector(
            'name',
            'description',
            config='english',
        )).filter(
            Q(search=query) |
            Q(name__istartswith=value)
        )

        if ft_search.exists():
            return ft_search

        name_search = queryset.filter(name__istartswith=value)
        if name_search.exists():
            return name_search

        general = queryset.filter(
            Q(name__icontains=value)
            | Q(description__icontains=value)
        )

        return general

    def get_nearest_curated_city(self, city, radius):

        if radius == EventFilter.FIVE_MILE:
            distance = 5
        elif radius == EventFilter.TWENTY_FIVE_MILE:
            distance = 25
        elif radius == EventFilter.FIFTY_MILE:
            distance = 50
        elif radius == EventFilter.HUNDRED_MILE:
            distance = 100
        elif radius == EventFilter.HUNDRED_PLUS_MILE:
            distance = 10000
        else:
            return False

        point = Point(y=city.point.y, x=city.point.x, srid=4326)
        cities = City.objects.filter(
            is_curated=True,
            point__dwithin=(point, D(mi=distance))
        )
        cities = cities.annotate(
            distance=Distance("point", point)
        ).order_by('distance')

        if not cities and city.slug in get_tampa():
            cities = City.objects.filter(
                slug='tampa-bay-florida-united-states'
            )

        return cities


class EventSearchViewSet(viewsets.ModelViewSet):
    serializer_class = LightBasicEventSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = EventSearchFilter
    pagination_class = EventPagination

    def get_queryset(self):
        return self.search_events()

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)

        key = self.request.query_params.get('search')
        response.data.update({"search_key": key})
        return response

    def search_events(self):
        query_params = self.request.query_params
        is_past_events = query_params.get('when', None)
        events = Event.objects.viewable().select_related('theme')
        if is_past_events and is_past_events == EventFilter.PAST_EVENTS:
            events = Event.objects.viewable(
                past_events=True
            ).select_related('theme', 'user')
        events = self.filter_search(events).distinct()
        return events

    def filter_search(self, queryset):
        value = self.request.query_params.get('search', '')
        if len(value) < 3:
            return queryset.none()

        self.serializer_class = LightBasicEventSerializer

        query = SearchQuery(
            value,
            config='english',
            search_type='phrase',
        )

        ft_search = queryset.annotate(search=SearchVector(
            'name',
            'description',
            config='english',
        )).filter(
            Q(search=query) |
            Q(name__istartswith=value)
        )

        if ft_search.exists():
            return ft_search

        name_search = queryset.filter(name__istartswith=value)
        if name_search.exists():
            return name_search

        general = queryset.filter(
            Q(name__icontains=value)
            | Q(description__icontains=value)
        )

        return general


class GuestListViewSet(core_views.GuestListViewSet):
    serializer_class = GuestListSerializer


class BlogUpdate(core_views.BlogUpdate):
    pass


class MigrateEventView(core_views.MigrateEventView):
    pass


class BlogViewSet(core_views.BlogViewSet):
    serializer_class = BlogSerializer

    def create(self, request):
        fields = [
            "title",
            "image_url",
            "url_guide",
            "description",
            "post_id",
            "tags",
            "city",
            "read_time",
            'date',
            'author_name',
            'author_image',
            "is_stuff_to_do",
        ]
        data = dict()
        empty_fields = []
        for field in fields:
            data[field] = request.data.get(field)
            if not data[field] and field != "tags":
                empty_fields.append(field)

        if empty_fields:
            raise RequiredFieldValidation(empty_fields)

        city = City.objects.filter(name=data["city"]).first()

        if not city:
            try:
                city = City.objects.get(slug='tampa-bay-florida-united-states')
            except City.DoesNotExist:
                content = {
                    "status_code": status.HTTP_404_NOT_FOUND,
                    "success": False,
                    "message": "Failed to create city Guide"
                }
                return Response(content)

        guide, _ = Blog.objects.update_or_create(
            post_id=data["post_id"],
            defaults={
                "city": city,
                "date": data["date"],
                "tags": data["tags"],
                "name": data["title"],
                "post_id": data["post_id"],
                "image_url": data["image_url"],
                "url": data["url_guide"],
                "reading_time": data["read_time"],
                "author_name": data["author_name"],
                "description": data["description"],
                "author_image": data["author_image"],
                "is_stuff_to_do": data["is_stuff_to_do"]
            }
        )
        if 'Neighborhoods' in data["tags"]:
            neighborhoods = Neighbourhood.objects.filter(
                city=city
            ).values_list('name', flat=True)
            neighborhood = set(data["tags"]).intersection(set(neighborhoods))
            neighborhoods = Neighbourhood.objects.filter(name__in=neighborhood)
            for neighborhood in neighborhoods:
                try:
                    NeighbourhoodGuide.objects.get_or_create(
                        neighborhood=neighborhood,
                        guides=guide,
                        defaults={
                            "guides": guide,
                            "neighborhood": neighborhood,
                        }
                    )
                except NeighbourhoodGuide.MultipleObjectsReturned:
                    pass

        content = {
            "status_code": status.HTTP_200_OK,
            "success": True,
            "message": "City Guide Created successfully"
        }
        return Response(content)


class CategoryViewSet(AddEventActionMixin, viewsets.ModelViewSet):
    queryset = Category.objects.viewable().order_by('name')
    serializer_class = CategorySerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend]
    filterset_class = CategoryLimitFilter
    lookup_field = 'slug'

    @action(detail=False)
    def all(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        queryset = queryset.order_by('name')
        queryset = self.filter_queryset(queryset)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=False)
    def tree(self, request, *args, **kwargs):
        queryset = self.get_queryset().filter(level=0)
        serializer = CategoryTreeSerializer(
            queryset,
            many=True,
            context={'request': request}
        )
        return Response(serializer.data)


class CityViewSet(core_views.CityViewSet):
    serializer_class = CitySerializer
    filter_backends = [DjangoFilterBackend]

    def get_queryset(self):
        if self.is_search():
            self.pagination_class = CitySearchPagination
            self.serializer_class = CitySearchSerializer
            cities = self.search_cities()
            return cities.exclude(latitude=0, longitude=0)

        return self.queryset

    def is_search(self):
        value = self.request.query_params.get('search', '')
        if value:
            return True

        return False

    def search_cities(self):
        cities = City.objects.all()
        cities = self.filter_search(cities)
        return cities

    def filter_search(self, queryset):
        value = self.request.query_params.get('search', '')

        name_search = queryset.filter(name__istartswith=value)
        if name_search.exists():
            return name_search

        query = SearchQuery(
            value,
            config='english',
            search_type='phrase',
        )

        ft_search = queryset.annotate(search=SearchVector(
            'name',
            'state',
            config='english',
        )).filter(
            Q(search=query) |
            Q(name__istartswith=value)
        )

        if ft_search.exists():
            return ft_search

        general = queryset.filter(
            Q(name__icontains=value)
            | Q(state__icontains=value)
        )

        return general


class EventDetailRelatedViewSet(core_views.EventDetailRelatedViewSet):
    serializer_class = EventDetailSerializer


class EventDetailViewSet(core_views.EventDetailViewSet):
    serializer_class = EventDetailSerializer


class EventOldDetailViewSet(core_views.EventOldDetailViewSet):
    serializer_class = EventDetailSerializer


class LocationViewSet(core_views.LocationViewSet):
    serializer_class = LocationSerializer


class PartnerViewSet(core_views.PartnerViewSet):
    serializer_class = PartnerSerializer


class ThemeViewSet(core_views.ThemeViewSet):
    serializer_class = ThemeSerializer


class UserViewSet(core_views.UserViewSet):
    serializer_class = UserSerializer


@api_view()
def health(request):
    return Response({'Status': 200})


@api_view()
def dbz_error(request):
    x = 2/0
    return Response({'Status': x})


class MobileEventViewSet(RelatedEventMixin, viewsets.ModelViewSet):
    serializer_class = EventSearchSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = EventSearchFilter

    def get_queryset(self):
        return self.search_events()

    def search_events(self):
        events = Event.objects.viewable().exclude(
            locations=None).select_related('theme')
        events = self.filter_search(events)
        return events

    def filter_search(self, queryset):
        value = self.request.query_params.get('search', '')
        if len(value) < 3:
            return queryset.none()

        self.serializer_class = EventSearchSerializer

        query = SearchQuery(
            value,
            config='english',
            search_type='phrase',
        )

        ft_search = queryset.annotate(search=SearchVector(
            'name',
            'description',
            config='english',
        )).filter(
            Q(search=query) |
            Q(name__istartswith=value)
        )

        if ft_search.exists():
            return ft_search

        general = queryset.filter(
            Q(name__icontains=value)
            | Q(description__icontains=value)
        )

        return general


@method_decorator(never_cache, name='dispatch')
class UserInvitationViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = UserInviteSerializer
    queryset = Invite.objects.all()
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['unation_user_id', "email_id"]

    def create(self, request):
        token = int(datetime.now().timestamp())
        user_name = request.data.get("username", None)
        email_id = request.data.get("email_id", None)

        if user_name:
            user = MongoUser.objects.filter(username=user_name).first()
        else:
            user = MongoUser.objects.filter(email=email_id).first()
        if not user:
            return Response({
                "success": False,
                "message": "User not found."
            }, status=status.HTTP_404_NOT_FOUND)

        invite, created = Invite.objects.get_or_create(
            unation_user_id=user._id,
            email_id=user.email,
            username=user.username)
        invite.invite_token = token
        invite.save()

        i_type = request.data["invite_type"]
        send_invite_email.delay(user.email, user.username, token, i_type)
        return Response({"success": True,
                         "message": 'Invite sent successfully'})


class EventsForYouViewSet(views.APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        user_id = request.GET.get('user_id')
        user = UserMigration.to_local(UserMigration, user_id)
        if not user:
            return Response({"error": "User does not exit"}, status=400)

        user_interests = user.interests.all()

        queryset = Event.objects.none()
        for category in user_interests:
            events = category.events.all()
            if events:
                queryset |= events
        queryset |= user.rsvped_events.all()

        serializer = LightEventSerializer(
            queryset,
            many=True,
            context={'request': request},
        )
        return Response(serializer.data)


class ReadonlyEventViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Event.objects.using("readonly").all()
    serializer_class = BasicEventSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend]
    filterset_class = EventFilter
    pagination_class = EventPagination
