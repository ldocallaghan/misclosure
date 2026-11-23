# Reference values for the kite-mcp conformance cases: each case run by
# calling KITE directly, encoded with modelcheck's R encoder. A port of
# kite-mcp's tests/conformance/reference.R.
#
# The generated baseline uses kite-mcp's worker/synthetic.R, so the
# environment variable KITE_MCP_DIR must name a kite-mcp checkout.
#
# Usage: Rscript --vanilla reference.R <out.json> <csv-dir>

args <- commandArgs(trailingOnly = TRUE)
out <- args[[1]]
csv_dir <- args[[2]]
here <- dirname(normalizePath(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE))))
source(file.path(here, "..", "..", "encoders", "r", "modelcheck_encode.R"))
kite_mcp <- Sys.getenv("KITE_MCP_DIR")
if (!nzchar(kite_mcp)) stop("set KITE_MCP_DIR to a kite-mcp checkout")
source(file.path(kite_mcp, "worker", "synthetic.R"))
suppressPackageStartupMessages({ library(KITE); library(data.table) })

mc_info <- function(info) {
  a <- info$accounting
  list(convergence = mc_scalar(info$convergence),
       criterion = mc_scalar(info$criterion),
       iterations = mc_scalar(info$iterations),
       accounting = list(ok = mc_scalar(a$ok), rule = mc_scalar(a$rule),
                         world_trade_balance = mc_scalar(a$world_trade_balance),
                         baseline_world_trade_balance = mc_scalar(a$baseline_world_trade_balance),
                         world_value_added = mc_scalar(a$world_value_added),
                         tolerance = mc_scalar(a$tolerance),
                         reason = mc_scalar(a$reason)))
}

mc_case <- function(info, output, warnings = list()) {
  tables <- list()
  for (n in names(output)) {
    if (is.data.frame(output[[n]])) tables[[n]] <- mc_table(output[[n]])
  }
  list(info = mc_info(info), tables = tables, warnings = warnings)
}

settings <- list(verbose = 0L, tolerance = 1e-4, vfactor = 0.1, max_iterations = 200)

run_case <- function(model, ic, scenario, settings) {
  warnings <- list()
  withCallingHandlers({
    result <- update_equilibrium(model = model, initial_conditions = ic,
                                 model_scenario = scenario, settings = settings)
    processed <- process_results(result)
  }, warning = function(w) {
    warnings[[length(warnings) + 1L]] <<- list(class = as.list(class(w)), message = conditionMessage(w))
    invokeRestart("muffleWarning")
  })
  mc_case(result$info, processed$output, warnings)
}

# `rate`, not `value`: inside data.table, `value` on the right of := names the column
tariff_war <- function(ic, rate = 1.2) {
  t <- data.table::copy(ic$tariff)
  t[origin == "c1" & destination == "c2", value := rate]
  t[origin == "c2" & destination == "c1", value := rate]
  stopifnot(sum(t$value == rate) == 2L * length(unique(t$sector)))
  t
}

cases <- list()

# KITE's own example, through run_kite_example()
source(system.file("examples", "example.R", package = "KITE"))
ex <- run_kite_example()
cases$example_cp <- mc_case(ex$cp$info, ex$cp_summary$output)
cases$example_chkw <- mc_case(ex$chkw$info, ex$chkw_summary$output)
cases$example_mw <- mc_case(ex$mw$info, ex$mw_summary$output)
ic <- ex$cp$initial_conditions
ic_carbon <- ex$mw$initial_conditions

# a generated economy: 5 countries, 3 sectors, seed 7
g <- kite_mcp_synthetic_baseline(5L, 3L, 7L)
gic <- g$initial_conditions
gic_carbon <- gic
gic_carbon$price <- g$price
gic_carbon$carbon_intensity <- g$carbon_intensity
cases$gen5x3_cp <- run_case(caliendo_parro_2015, gic, list(tariff_new = tariff_war(gic)), settings)
cases$gen5x3_chkw <- run_case(chowdhry_hinz_kamin_wanner_2022, gic, list(tariff_new = tariff_war(gic)), settings)
cases$gen5x3_mw <- run_case(mahlkow_wanner_2021, gic_carbon,
                            list(carbon_tax = 0.5, countries_climate_club = c("c1", "c3"),
                                 scenario_carbon_tariff = TRUE, scenario_export_rebate = TRUE),
                            settings)

# a carbon tax that R's parser reads 1 bit away from its correctly rounded value;
# the server reads the JSON number 0.00648016 with correct rounding
cases$awkward_literal_mw <- run_case(mahlkow_wanner_2021, ic_carbon,
                                     list(carbon_tax = 0x1.a8af0b45d9531p-8, countries_climate_club = c("c1", "c3"),
                                          scenario_carbon_tariff = TRUE, scenario_export_rebate = TRUE),
                                     settings)

# a tariff on one sector only: c1 levies 25 % on s1 imports from everyone
st <- data.table::copy(ic$tariff)
st[destination == "c1" & sector == "s1" & origin != destination, value := 1.25]
cases$sector_tariff_cp <- run_case(caliendo_parro_2015, ic, list(tariff_new = st), settings)

# does not converge: fixed_country_share after the tariff war
cases$nonconv_cp <- run_case(caliendo_parro_2015, ic, list(tariff_new = tariff_war(ic)),
                             c(settings, list(trade_balance_rule = "fixed_country_share")))

# a scenario variable the model does not read
cases$unread_chkw <- run_case(chowdhry_hinz_kamin_wanner_2022, ic,
                              list(tariff_new = tariff_war(ic), carbon_tax = 0.5), settings)

# the example's baseline as CSV files, values with 17 significant digits, for the server to read
dir.create(csv_dir, showWarnings = FALSE, recursive = TRUE)
tabs <- ic_carbon
tabs$trade_elasticity <- tabs$elasticities$trade_elasticity
tabs$elasticities <- NULL
for (n in names(tabs)) {
  dt <- data.table::copy(tabs[[n]])
  if (is.double(dt$value)) dt[, value := sprintf("%.17g", value)]
  data.table::fwrite(dt, file.path(csv_dir, paste0(n, ".csv")))
}

d <- utils::packageDescription("KITE")
model <- list(name = "KITE", version = d$Version,
              commit = if (is.null(d$RemoteSha)) NULL else d$RemoteSha,
              r_version = paste(R.version$major, R.version$minor, sep = "."))
mc_write_reference(cases, model, out)
cat("wrote", length(cases), "cases to", out, "\n")
