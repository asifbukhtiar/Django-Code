from datetime import timedelta

from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.geos import MultiPoint, Point
from django.contrib.gis.measure import D
from django.db.models import Count, Q
from django.utils import timezone
from django_filters import rest_framework as filters

from core.models import (
    Ad,
    Blog,
    Category,
    City,
    Event,
    Neighbourhood,
    NeighbourhoodGuide,
    Section,
    State,
    Venue,
)
from core.utils import (
    get_city_day_start,
    get_next_week,
    get_next_weekend,
    get_tampa,
    get_this_week,
    get_weekend,
    to_city_time,
)
from core.v2.validators import RequiredFieldValidation
from experiences.models import Experience


class EventFilter(filters.FilterSet):
    FREE = 'free'
    PRICE_10 = 'price_10'
    PRICE_25 = 'price_25'
    PRICE_50 = 'price_50'
    PRICE_100 = 'price_100'
    PRICE_100_plus = 'price_100_plus'

    PRICE_CHOICES = [
        [FREE, 'Free'],
        [PRICE_10, 'Up to $10'],
        [PRICE_25, 'Up to $25'],
        [PRICE_50, 'Up to $50'],
        [PRICE_100, 'Up to $100'],
        [PRICE_100_plus, '$100+'],
    ]

    NOW = 'now'
    TODAY = 'today'
    TOMORROW = 'tomorrow'
    WEEKEND = 'weekend'
    NEXT_WEEK = 'next-week'
    NEXT_WEEKEND = 'next-weekend'
    THIS_WEEK = 'this-week'
    HAPPENING_LATER = 'happening-later'
    PAST_EVENTS = 'past'

    WHEN_CHOICES = [
        [NOW, 'Happening Now'],
        [TODAY, 'Today'],
        [TOMORROW, 'Tomorrow'],
        [THIS_WEEK, 'This Week'],
        [WEEKEND, 'This Weekend'],
        [NEXT_WEEK, 'Next Week'],
        [NEXT_WEEKEND, 'Next Weekend'],
        [HAPPENING_LATER, 'Happening Later'],
        [PAST_EVENTS, 'Past'],
    ]

    FIVE_MILE = 'mile-5'
    TWENTY_FIVE_MILE = 'mile-25'
    FIFTY_MILE = 'mile-50'
    HUNDRED_MILE = 'mile-100'
    HUNDRED_PLUS_MILE = 'mile-100-plus'
    ONE_HUNDRED_FIFTY_MILE = 'mile-150'
    TWO_HUNDRED_MILE = 'mile-200'
    TWO_HUNDRED_FIFTY_MILE = 'mile-250'
    THREE_HUNDRED_MILE = 'mile-300'
    THREE_HUNDRED_FIFTY_MILE = 'mile-350'

    RADIUS_CHOICES = [
        [FIVE_MILE, '5mi'],
        [TWENTY_FIVE_MILE, '25mi'],
        [FIFTY_MILE, '50mi'],
        [HUNDRED_MILE, '100mi'],
        [HUNDRED_PLUS_MILE, '100+ mi'],
        [ONE_HUNDRED_FIFTY_MILE, '150mi'],
        [TWO_HUNDRED_MILE, '200mi'],
        [TWO_HUNDRED_FIFTY_MILE, '250mi'],
        [THREE_HUNDRED_MILE, '300mi'],
        [THREE_HUNDRED_FIFTY_MILE, '350mi'],
    ]

    price = filters.ChoiceFilter(
        choices=PRICE_CHOICES,
        method='filter_price',
        label='Ticket Price',
    )
    price_sections = filters.ChoiceFilter(
        choices=PRICE_CHOICES,
        method='filter_price_sections',
        label='Ticket Price',
    )

    unation_event_id = filters.NumberFilter()
    is_featured = filters.BooleanFilter(label='Is Featured')

    is_curated = filters.BooleanFilter(
        method='filter_has_location',
        label='Is Curated',
    )

    when = filters.MultipleChoiceFilter(
        choices=WHEN_CHOICES,
        method='filter_when',
        label='When',
    )

    date = filters.DateFilter(
        field_name='start_date__date',
        label='Start Date',
    )

    since = filters.DateTimeFilter(
        method='filter_until_since',
        label='Since',
    )

    until = filters.DateTimeFilter(
        method='filter_until_since',
        label='Until',
    )

    recent = filters.BooleanFilter(
        method='filter_recent',
        label='Recent',
    )

    search = filters.CharFilter(
        method='filter_search',
        label='Search',
    )

    what = filters.ModelMultipleChoiceFilter(
        queryset=Category.objects.viewable(),
        label='What',
        field_name='eventcategory__sub_category__slug',
        to_field_name='slug',
    )

    radius = filters.ChoiceFilter(
        choices=RADIUS_CHOICES,
        method='filter_radius',
        label='Radius',
    )

    limit = filters.NumberFilter(method="event_limit", label="limit")
    city = filters.CharFilter(method='filter_city', label='City')

    latitude = filters.NumberFilter(method='filter_map_center',
                                    label='Latitude')

    longitude = filters.NumberFilter(method='filter_map_center',
                                     label='Longitude')

    map_radius = filters.NumberFilter(method='filter_map_radius',
                                      label='Visible Radius')

    has_location = filters.BooleanFilter(
        method='filter_has_location',
        label='Has Location',
    )

    upcoming = filters.BooleanFilter(
        method='filter_upcoming',
        label='Upcoming'
    )

    registered_user = filters.BooleanFilter(
        method='filter_registered_user',
        label='Registered User'
    )

    ongoing = filters.BooleanFilter(
        method='filter_ongoing',
        label='Ongoing'
    )
    venue = filters.CharFilter(
        method='filter_venue',
        label='Venue'
    )
    state = filters.CharFilter(method='filter_state', label='State')
    page_size = filters.NumberFilter(
        method='filter_page_size',
        label='Page Size'
    )
    is_virtual = filters.BooleanFilter(label='Is Virtual')

    created_after = filters.CharFilter(
        method='filter_created_after',
        label='Created After',
    )
    updated_after = filters.CharFilter(
        method='filter_updated_after',
        label='Updated After',
    )

    class Meta:
        model = Event
        fields = [
            'price',
            'map_radius',
            'is_featured',
            'is_curated',
            'when',
            'date',
            'recent',
            'search',
            'radius',
            'what',
            'city',
            'since',
            'until',
            'venue',
            'state',
            'updated_at',
            'created_at',
        ]

    def filter_venue(self, queryset, name, value):
        if value:
            return queryset.filter(locations__venue__slug=value)
        return queryset

    def filter_page_size(self, queryset, name, value):
        return queryset

    def filter_ongoing(self, queryset, name, value):
        if not value:
            utc_now = timezone.now()
            return queryset.exclude(
                start_date__lte=utc_now,
                end_date__gte=utc_now,
            )

        return queryset

    def filter_registered_user(self, queryset, name, value):
        return queryset

    def filter_upcoming(self, queryset, name, value):
        if value:
            return queryset.filter(start_date__gte=timezone.now())
        return queryset

    def filter_map_center(self, queryset, name, value):
        return queryset

    def filter_map_radius(self, queryset, name, value):
        latitude = self.data.get("latitude", None)
        longitude = self.data.get("longitude", None)
        fields = []

        if not latitude:
            fields.append('latitude')

        if not longitude:
            fields.append('longitude')

        if fields:
            raise RequiredFieldValidation(fields)

        latitude = float(latitude)
        longitude = float(longitude)
        ref_point = Point(longitude, latitude)

        one_mile = 1609
        visible_radius = float(value) * one_mile

        return queryset.filter(point__dwithin=(ref_point, D(m=visible_radius)))

    def filter_has_location(self, queryset, name, value):
        qs = queryset

        if name == 'is_curated':
            if value:
                qs = qs.filter(
                    Q(is_staff_picked=value)
                    | Q(is_featured=value)
                )
            else:
                qs = qs.filter(is_staff_picked=value)
            if not value:
                return qs

        if value:
            # qs = qs.annotate(location_count=Count('eventlocation__event_id'))
            # qs = qs.filter(location_count__gt=0)
            qs = qs.filter(has_location=Event.YES)

        return qs

    def filter_price(self, queryset, name, value):
        return queryset.filter(prices_available__contains=[value])

    def filter_price_sections(self, queryset, name, value):
        queryset = queryset.filter(prices_available__contains=[value])
        if value == EventFilter.PRICE_10:
            return queryset.exclude(
                prices_available__contains=[EventFilter.FREE]
            )
        elif value == EventFilter.PRICE_25:
            return queryset.exclude(
                Q(prices_available__contains=[EventFilter.FREE]) |
                Q(prices_available__contains=[EventFilter.PRICE_10])
            )
        elif value == EventFilter.PRICE_50:
            return queryset.exclude(
                Q(prices_available__contains=[EventFilter.FREE]) |
                Q(prices_available__contains=[EventFilter.PRICE_10]) |
                Q(prices_available__contains=[EventFilter.PRICE_25])
            )
        elif value == EventFilter.PRICE_100:
            return queryset.exclude(
                Q(prices_available__contains=[EventFilter.FREE]) |
                Q(prices_available__contains=[EventFilter.PRICE_10]) |
                Q(prices_available__contains=[EventFilter.PRICE_25]) |
                Q(prices_available__contains=[EventFilter.PRICE_50])
            )
        elif value == EventFilter.PRICE_100_plus:
            return queryset.exclude(
                Q(prices_available__contains=[EventFilter.FREE]) |
                Q(prices_available__contains=[EventFilter.PRICE_10]) |
                Q(prices_available__contains=[EventFilter.PRICE_25]) |
                Q(prices_available__contains=[EventFilter.PRICE_50]) |
                Q(prices_available__contains=[EventFilter.PRICE_100])
            )

        return queryset

    def filter_when(self, queryset, name, value):
        tampa_bay_slug = 'tampa-bay-florida-united-states'
        city_slug = self.request.GET.get("city", tampa_bay_slug)
        try:
            city = City.objects.get(slug=city_slug)
        except City.DoesNotExist:
            return queryset

        start = get_city_day_start(city)
        start = to_city_time(city, start)
        end = start + timedelta(hours=23, minutes=59, seconds=59)

        when_filter = Q()

        if self.NOW in value:
            utc_now = timezone.now()
            when_filter = Q(start_date__lte=utc_now) & Q(end_date__gte=utc_now)

        if self.TODAY in value:
            when_filter |= Q(start_date__range=[start, end])

        if self.TOMORROW in value:
            start_date = start + timedelta(days=1)
            end_date = end + timedelta(days=1)
            when_filter |= Q(start_date__range=[start_date, end_date])

        if self.WEEKEND in value:
            start_date, end_date = get_weekend(city)
            start_date = to_city_time(city, start_date)
            end_date = to_city_time(city, end_date)
            when_filter |= Q(start_date__range=[start_date, end_date])

        if self.NEXT_WEEK in value:
            start_date, end_date = get_next_week(city)
            start_date = to_city_time(city, start_date)
            end_date = to_city_time(city, end_date)
            when_filter |= Q(start_date__range=[start_date, end_date])

        if self.NEXT_WEEKEND in value:
            start_date, end_date = get_next_weekend(city)
            start_date = to_city_time(city, start_date)
            end_date = to_city_time(city, end_date)
            when_filter |= Q(start_date__range=[start_date, end_date])

        if self.THIS_WEEK in value:
            start_date, end_date = get_this_week(city)
            start_date = to_city_time(city, start_date)
            end_date = to_city_time(city, end_date)
            when_filter |= Q(start_date__range=[start_date, end_date])

        if self.HAPPENING_LATER in value:
            start_date, end_date = get_next_weekend(city)
            end_date = to_city_time(city, end_date)
            when_filter |= Q(start_date__gte=end_date)

        if self.PAST_EVENTS in value:
            utc_now = timezone.now()
            when_filter |= Q(end_date__lte=utc_now)

        since, until = self.process_since_and_until(city)

        if since and until:
            when_filter |= Q(start_date__range=[since, until])

        elif until:
            when_filter |= Q(start_date__lte=until)

        elif since:
            when_filter |= Q(start_date__gte=since)

        queryset = queryset.filter(when_filter)

        return queryset.distinct()

    def process_since_and_until(self, city):
        since = self.form.cleaned_data.get('since', None)
        if since:
            since = to_city_time(city, since)

        until = self.form.cleaned_data.get('until', None)
        if until:
            until = until.replace(hour=23, minute=59, second=59)
            until = to_city_time(city, until)

        return since, until

    def filter_recent(self, queryset, name, value):
        return queryset.order_by('-created_at')

    def filter_until_since(self, queryset, name, value):
        if self.form.cleaned_data.get('when'):
            return queryset

        city_slug = self.request.GET.get("city", None)

        if city_slug:
            try:
                city = City.objects.get(slug=city_slug)
            except City.DoesNotExist:
                return queryset

            since, until = self.process_since_and_until(city)

            if since and until:
                date_filter = Q(start_date__range=[since, until])

            elif until:
                date_filter = Q(start_date__lte=until)

            elif since:
                date_filter = Q(start_date__gte=since)

            return queryset.filter(date_filter)

        return queryset

    def filter_search(self, queryset, name, value):
        return queryset

    def filter_radius(self, queryset, name, value):
        city = self.request.GET.get("city")
        longitude = self.request.GET.get("longitude")
        latitude = self.request.GET.get("latitude")

        """ TODO: #2208 revert changes to
        if not (city or (longitude and latitude))
        or value == self.HUNDRED_PLUS_MILE: """

        if not (city or (longitude and latitude)):
            return queryset
        ref_point = None

        if city or longitude and latitude:
            add_miles = None
            try:
                city = City.objects.get(slug=city)
            except City.DoesNotExist:
                if longitude and latitude:
                    ref_point = Point(x=float(longitude), y=float(latitude))
                else:
                    return queryset.none()
            else:
                ref_point = city.point
                if city.slug in get_tampa():
                    cities = City.objects.filter(
                        slug__in=get_tampa()
                    ).annotate(distance=Distance("point", ref_point))
                    cities = cities.exclude(slug=city.slug)
                    if cities:
                        multipoint = MultiPoint(
                            cities.first().point,
                            city.point,
                        )
                        ref_point = multipoint.centroid
                        add_miles = cities.first().distance.m / 1.999

        one_mile = 1609

        if value == self.FIVE_MILE:
            distance = 5 * one_mile
        elif value == self.TWENTY_FIVE_MILE:
            distance = 25 * one_mile
        elif value == self.FIFTY_MILE:
            distance = 50 * one_mile
        elif value == self.HUNDRED_MILE:
            distance = 100 * one_mile

        # TODO: #2208 revert changes to value == self.ONE_HUNDRED_FIFTY_MILE

        elif value == self.ONE_HUNDRED_FIFTY_MILE or value == self.HUNDRED_PLUS_MILE: # noqa
            distance = 150 * one_mile
        elif value == self.TWO_HUNDRED_MILE:
            distance = 200 * one_mile
        elif value == self.TWO_HUNDRED_FIFTY_MILE:
            distance = 250 * one_mile
        elif value == self.THREE_HUNDRED_MILE:
            distance = 300 * one_mile
        elif value == self.THREE_HUNDRED_FIFTY_MILE:
            distance = 350 * one_mile

        if add_miles:
            distance = distance + add_miles

        return queryset.filter(point__dwithin=(ref_point, D(m=distance)))

    def event_limit(self, queryset, name, value):
        return queryset[:value]

    def filter_city(self, queryset, name, value):
        return queryset

    def filter_state(self, queryset, name, value):
        cities = City.objects.filter(state_new__slug=value)
        if not cities.exists():
            cities = City.objects.filter(state_new__state_code__iexact=value)
        queryset = queryset.filter(eventlocation__location__city__in=cities)
        return queryset

    def filter_created_after(self, queryset, name, value):
        return queryset.filter(created_at__gte=value)

    def filter_updated_after(self, queryset, name, value):
        return queryset.filter(updated_at__gte=value)


class EventSearchFilter(EventFilter):
    search = filters.CharFilter(
        method='filter_search',
        label='Search',
        required=True,
    )

    state = filters.CharFilter(
        method='filter_state',
        label='State',
    )

    class Meta:
        model = Event
        fields = [
            'price',
            'is_featured',
            'is_curated',
            'when',
            'date',
            'recent',
            'search',
            'radius',
            'what',
            'city',
            'since',
            'until',
        ]

    def filter_search(self, queryset, name, value):
        return queryset

    def filter_state(self, queryset, name, value):
        cities = City.objects.filter(state_new__state_code__iexact=value)
        queryset = queryset.filter(eventlocation__location__city__in=cities)
        return queryset


class MapEventFilter(EventFilter):
    radius = filters.ChoiceFilter(
        choices=EventFilter.RADIUS_CHOICES,
        method='filter_radius',
        label='Radius',
        required=True,
    )

    city = filters.CharFilter(
        method='filter_city',
        label='City',
        required=True,
    )

    class Meta:
        model = Event
        fields = [
            'price',
            'map_radius',
            'is_featured',
            'is_curated',
            'when',
            'date',
            'recent',
            'search',
            'radius',
            'what',
            'city',
            'since',
            'until',
        ]


class ExperienceFilter(filters.FilterSet):
    city = filters.CharFilter(method='filter_city', label='City')
    state = filters.CharFilter(method='filter_state', label='State')

    class Meta:
        model = Experience
        fields = ['city']

    def filter_city(self, queryset, name, value):
        if value == 'tampa-bay-florida-united-states':
            return queryset.filter(
                neighbourhood__city__in=City.objects.tampa_bay_cities(),
            )
        else:
            return queryset.filter(neighbourhood__city__slug=value)

    def filter_state(self, queryset, name, value):
        cities = City.objects.filter(state_new__state_code__iexact=value)
        queryset = queryset.filter(neighbourhood__city__in=cities)
        return queryset


class SectionFilter(filters.FilterSet):
    city = filters.CharFilter(method='filter_city', label='City')
    platform = filters.ChoiceFilter(
        choices=Section.PLATFORM_CHOICES,
        label='Platform',
    )
    page_type = filters.ChoiceFilter(
        choices=Section.PAGE_TYPE_CHOICES,
        label='Page Type',
    )
    tab = filters.ChoiceFilter(
        choices=Section.TAB_CHOICES,
        label='Tab'
    )

    class Meta:
        model = Section
        fields = ['city']

    def filter_city(self, queryset, name, value):

        try:
            if value in get_tampa():
                slug = 'tampa-bay-florida-united-states'
                city = City.objects.get(slug=slug)
            else:
                city = City.objects.get(slug=value)
        except City.DoesNotExist:
            return queryset.filter(is_curated=False)

        if not city.is_curated:
            return queryset.filter(is_curated=False)

        queryset = queryset.filter(city=city)
        return queryset


class StateSectionFilter(SectionFilter):
    state = filters.CharFilter(
        method='filter_state',
        label='State',
        required=True,
    )

    class Meta:
        model = Section
        fields = '__all__'

    def filter_state(self, queryset, name, value):
        state_code = self.request.query_params.get('state')
        try:
            State.objects.get(
                Q(state_code__iexact=state_code) |
                Q(name__iexact=state_code)
            )
        except State.DoesNotExist:
            return queryset.none()
        except State.MultipleObjectsReturned:
            return queryset

        return queryset


class BlogFilter(filters.FilterSet):
    city = filters.CharFilter(method='filter_city', label='City')

    class Meta:
        model = Blog
        fields = ['city']

    def filter_city(self, queryset, name, value):
        if value == 'tampa-bay-florida-united-states':
            return queryset.filter(
                city__in=City.objects.tampa_bay_cities(),
            )
        else:
            return queryset.filter(city__slug=value)


class VenueFilter(filters.FilterSet):
    city = filters.CharFilter(method='filter_city', label='City')

    class Meta:
        model = Venue
        fields = ['city']

    def filter_city(self, queryset, name, value):
        """
        We need to add all filters here because multi-spanned relationship
        in Django multiple joins. See the commit message for more info.
        """
        active = Q(locations__events__status=Event.ACTIVE)
        public = Q(locations__events__event_type=Event.PUBLIC)
        future = Q(locations__events__end_date__gte=timezone.now())

        if value == 'tampa-bay-florida-united-states':
            city = Q(locations__city__in=City.objects.tampa_bay_cities())
        else:
            city = Q(locations__city__slug=value)

        return queryset.annotate(
            Count('id')
        ).filter(future & city & active & public)


class NeighbourhoodFilter(filters.FilterSet):
    city = filters.CharFilter(method='filter_city', label='City')
    state = filters.CharFilter(method='filter_state', label='State')

    class Meta:
        model = Neighbourhood
        fields = ['city']

    def filter_city(self, queryset, name, value):
        if value == 'tampa-bay-florida-united-states':
            return queryset.filter(
                city__in=City.objects.tampa_bay_cities(),
            )
        else:
            return queryset.filter(city__slug=value)

    def filter_state(self, queryset, name, value):
        cities = City.objects.filter(state_new__state_code__iexact=value)
        queryset = queryset.filter(city__in=cities)
        return queryset


class CategoryLimitFilter(filters.FilterSet):
    limit = filters.NumberFilter(method='category_limit', label='limit')

    class Meta:
        model = Category
        fields = '__all__'

    def category_limit(self, queryset, name, value):
        return queryset[:value]


class NeighbourhoodGuideFilter(filters.FilterSet):
    city = filters.CharFilter(method='filter_city', label='City')

    class Meta:
        model = NeighbourhoodGuide
        fields = ['city']

    def filter_city(self, queryset, name, value):
        if value == 'tampa-bay-florida-united-states':
            return queryset.filter(
                city__in=City.objects.tampa_bay_cities(),
            )
        else:
            return queryset.filter(city__slug=value)


class AdFilter(filters.FilterSet):
    city = filters.CharFilter(method='filter_city', label='City')

    class Meta:
        model = Ad
        fields = ['city']

    def filter_city(self, queryset, name, value):
        if value == 'tampa-bay-florida-united-states':
            return queryset.filter(
                city__in=City.objects.tampa_bay_cities(),
            )
        else:
            return queryset.filter(city__slug=value)
