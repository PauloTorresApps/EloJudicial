from django.urls import path

from . import views_processos, views_sistemas as views

app_name = 'sistemas'

urlpatterns = [
    path('', views.lista, name='lista'),
    path('<int:pk>/', views.ambientes, name='ambientes'),
    path('<int:pk>/senha/', views.informar_senha, name='informar_senha'),
    path('<int:pk>/senha/esquecer/', views.esquecer_senha, name='esquecer_senha'),
    path('<int:sistema_pk>/ambientes/<int:pk>/', views.servicos, name='servicos'),
    path('<int:sistema_pk>/ambientes/<int:pk>/avisos/', views.avisos_pendentes, name='avisos'),
    path('<int:sistema_pk>/ambientes/<int:pk>/padrao/', views.definir_padrao, name='definir_padrao'),
    path('padrao/remover/', views.remover_padrao, name='remover_padrao'),
    path('processo/', views_processos.buscar, name='buscar_processo'),
    path('<int:sistema_pk>/ambientes/<int:pk>/processos/<str:numero>/', views_processos.processo, name='processo'),
    path('<int:sistema_pk>/ambientes/<int:pk>/processos/<str:numero>/dados/', views_processos.processo_dados, name='processo_dados'),
    path('<int:sistema_pk>/ambientes/<int:pk>/processos/<str:numero>/documentos/<str:id_documento>/',
         views_processos.documento, name='documento'),
]
