# Zenodo sandbox for the [Zenodo publisher tool](https://github.com/weberBen/zenodo-publisher)

Dummy example of supported repo for the [Zenodo publisher tool](https://github.com/weberBen/zenodo-publisher)

## Testing info

Make test using the [sandbox](https://sandbox.zenodo.org/) of zenodo.

See [policy](https://github.com/zenodo/zenodo/issues/2373#issuecomment-3825099921) for deletation on sandbox after testing.

[Zenodo](https://help.zenodo.org/docs/deposit/manage-records/#:~:text=affect%20your%20DOI.-,Delete%20records,new%20version%20with%20corrected%20files.) introduces a 30-day deletion option. And you can apply restricted view to the sandbox record to limit the visibility of the content during testing.

After deltation, the record page only display theses information :
```
	Gone
The record you are trying to access was removed from Zenodo. The metadata of the record is kept for archival purposes.

Reason for removal: Test upload of a record

Removed by: Owner

Deletion policy: Record owners can delete their records within 30 days of publishing.

Date of removal: January 30, 2026

Citation: <author name> (<date>). <record title>. Zenodo. https://doi.org/10.5072/zenodo.<record id>

Identifier: 10.5072/zenodo.<record id>
```

And in sandbox, the doi does not resolve.

## Usage

- Clone the repo
- Create a new token [here](https://sandbox.zenodo.org/account/settings/applications/tokens/new/). Allow `deposit:actions`and `deposit:write`
- Adapt the `zenodo.env.example` to `.zenodo.env`
- Make modifications
- Use the publisher tool and follow given instructions
