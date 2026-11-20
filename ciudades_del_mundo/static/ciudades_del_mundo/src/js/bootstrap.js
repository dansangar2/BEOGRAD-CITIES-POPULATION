  document.addEventListener("DOMContentLoaded", function () {
    runPageInitializer("select2", function () { initSelect2(document); });
    runPageInitializer("editor de configuraci\u00f3n", function () { initConfigEditor(document); });
    runPageInitializer("layout de estad\u00edsticas", initStatsSplitChildrenLayout);
    runPageInitializer("estado de idioma", function () { initLanguageStatePreservation(document); });
    runPageInitializer("formularios con carga", function () { initLoadingSubmitForms(document); });
    runPageInitializer("enlaces con carga", function () { initLoadingLinks(document); });
    runPageInitializer("borrado de divisiones en grupos", function () { initGroupDivisionDeleteForms(document); });
    runPageInitializer("arbol de entidades de pais nuevo", function () { initNewCountryConfigTree(document); });
    runPageInitializer("ventana de entidad", function () { initNewCountryEntityDialog(document); });
    runPageInitializer("filas clicables", function () { initClickableRows(document); });
    runPageInitializer("tarjetas de grupos", function () { initGroupCountryCards(document); });
    runPageInitializer("campos de codigo", function () { initUppercaseCodeInputs(document); });
    runPageInitializer("tarjetas de paises nuevos", function () { initNewCountryResultCards(document); });
    runPageInitializer("pesta\u00f1as de configuraci\u00f3n", function () { initConfigTabs(document); });
    runPageInitializer("restauraci\u00f3n de idioma", function () {
      restorePageStateAfterLanguageChange();
    });
    runPageInitializer("tema", function () { initThemeSelector(document); });
    runPageInitializer("fechas locales", function () { initLocalDateTimes(document); });
    runPageInitializer("detalle de tareas", function () { initTaskDetailLog(document); });
    runPageInitializer("bootstrap de configuraciones", function () { initConfigBootstrap(document); });
    runPageInitializer("tablas as\u00edncronas", function () { initAsyncTables(document); });
    runPageInitializer("tablas de configuraci\u00f3n", function () { initConfigTables(document); });
    runPageInitializer("puntos de carga", function () { initConfigLoadingDots(document); });
    runPageInitializer("acciones de tareas", function () { initConfigTaskActions(document); });
    runPageInitializer("acciones de exportaci\u00f3n", function () { initConfigExportActions(document); });
    runPageInitializer("botones de configuraci\u00f3n", function () { refreshConfigActionButtons(document); });
    runPageInitializer("enlaces de retorno de grupos", function () { initGroupReturnLinks(document); });
    runPageInitializer("gr\u00e1ficos", function () { initDataCharts(document); });
    runPageInitializer("detalle de pa\u00eds en dashboard", function () { initDashboardCountryDetail(document); });
    runPageInitializer("estad\u00edsticas por pa\u00eds", function () { initStatsCountries(document); });
    runPageInitializer("restauraci\u00f3n de grupos", function () { restoreGroupReturnState(document); });
    runPageInitializer("mapa", initMap);
    runPageInitializer("identidad visual", initVisualIdentity);
  });
  window.addEventListener("pageshow", function (event) {
    if (event.persisted) {
      restoreGroupReturnState(document);
    }
  });
})();
