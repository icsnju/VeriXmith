#!/usr/bin/env Rscript

library(tidyverse)
setwd("tools/analysis")

write_table <- function(full_result) {
  require(gt)
  require(dplyr)
  require(readr)
  require(stringr)
  require(tidyr)

  full_result %>%
    filter(Completeness == TRUE) %>%
    select(!Completeness) %>%
    group_by(InputSets) %>%
    arrange(Mutants, Validations) %>%
    pivot_wider(
      names_from = Validations, names_glue = "{Validations}_{.value}",
      values_from = c(Crashes, Differences)
    ) %>%
    gt(
      rowname_col = "Mutants",
      groupname_col = "InputSets"
    ) %>%
    tab_stubhead(label = "#Mutants") %>%
    tab_spanner(
      label = "#Comparees",
      columns = everything()
    ) %>%
    tab_spanner(
      label = "#Crashes",
      columns = matches("\\d+_Crashes")
    ) %>%
    tab_spanner(
      label = "#Differences",
      columns = matches("\\d+_Differences")
    ) %>%
    tab_options(row_group.as_column = TRUE) %>%
    data_color(
      method = "numeric",
      domain = c(0, 25),
      palette = "ggsci::blue_grey_material"
    ) %>%
    gtsave(filename = "table.html")
}

c("sv-v", "sv-smt", "v-smt") %>%
  map(\(x) read_csv(str_glue(x, ".csv"))) %>%
  bind_rows() %>%
  write_table()
