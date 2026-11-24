from django.urls import path

from . import views

app_name = 'ingestao'

urlpatterns = [
    path('', views.lotes, name='lotes'),
    path('sistemas/<int:sistema_pk>/ambientes/<int:pk>/processos/<str:numero>/', views.ingerir, name='ingerir'),
    path('lotes/<int:pk>/', views.lote, name='lote'),
    path('lotes/<int:pk>/andamento/', views.lote_andamento, name='andamento'),
    path('lotes/<int:pk>/retomar/', views.retomar, name='retomar'),
    path('arquivos/<int:pk>/', views.original, name='original'),
    path('arquivos/<int:pk>/texto/', views.texto, name='texto'),
]
