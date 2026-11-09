# NIAID Data Ecosystem schemas
Schemas developed to standardize metadata for the NIAID Data Ecosystem

[View and extend the NIAID Data Ecosystem schemas on the Data Discovery Engine](https://discovery.biothings.io/view/niaid)

## To alter the base schemas
1. Edit one of the individual classes, stored in a .json file. Note that properties which are shared between multiple classes should go in the [`commonProperties.json`](https://github.com/NIAID-Data-Ecosystem/nde-schemas/blob/main/commonProperties.json) file to avoid them being defined in two places.
2. View the class on the [Data Discovery Engine Schema Playground](https://discovery.biothings.io/schema-playground). Note: you might get "missing class" warnings, if the definition is located in a separate file
3. Run `combine_nde_schema.py` to generate the combined schemas, stored in `combined_schema_DO_NOT_EDIT/NIAID_schema.json`. The definitions from all the .json files will be combined together.
4. Verify the [combined schema](https://raw.githubusercontent.com/NIAID-Data-Ecosystem/nde-schemas/main/combined_schema_DO_NOT_EDIT/NIAID_schema.json) on the [Data Discovery Engine Schema Playground](https://discovery.biothings.io/schema-playground)
5. Register or update the schema on the Data Discovery Engine.
