from django.urls import path

from . import views


app_name = "core"

urlpatterns = [
    # Home & FAQ
    path("", views.home, name="home"),
    path("faq/", views.faq, name="faq"),
    # Player views
    path("player/<str:player>/", views.player_detail, name="player_detail"),
    path(
        "player/<str:player>/deck/<str:event>/", views.deck_detail, name="deck_detail"
    ),
    path(
        "player/<str:player>/deck/<str:event>/<int:deck_index>/",
        views.deck_detail,
        name="deck_detail_disambiguated",
    ),
    # Search API
    path("api/search-index/", views.search_index, name="search_index"),
    # Format views
    path("<str:format>/", views.format_overview, name="format_overview"),
    path("<str:format>/tournaments/", views.tournament_list, name="tournament_list"),
    path(
        "<str:format>/tournaments/<str:event>/",
        views.tournament_detail,
        name="tournament_detail",
    ),
    path(
        "<str:format>/archetype/<str:archetype>/",
        views.archetype_detail,
        name="archetype_detail",
    ),
    path("<str:format>/cards/", views.cards_list, name="cards_list"),
    path("<str:format>/leaderboard/", views.leaderboard, name="leaderboard"),
]
