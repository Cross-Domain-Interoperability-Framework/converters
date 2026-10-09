#!/usr/bin/env python3
"""Resolve the unprefixed names in a schema.org JSON-LD record to CURIEs.

Shared by harvesters/geocodes_harvester.py and soso/ConvertFromSOSO.py, which
each carried their own copy until the copies drifted: the harvester's was
fixed to read the source @context, the SOSO converter's still decided every
name from a fixed list. One module keeps them from drifting again.

A name is resolved, in order:

  1. through the source @context, when the context defines it (PANGAEA's
     "conformsTo": "dct:conformsTo" stays dct:, not schema:);
  2. as schema: when it is a known schema.org property or type name;
  3. as schema: when the source context makes schema.org the default
     vocabulary -- an @vocab of schema.org, or the schema.org context given
     by URL ("@context": "http://schema.org"). Then every unprefixed name is a
     schema.org term by definition, and the fixed lists, which hold only a
     subset, must not decide it;
  4. otherwise as unk: (https://ex.org/unknown/), reported to the caller.

Standard library only.
"""

UNKNOWN_NS = "https://ex.org/unknown/"
UNKNOWN_PREFIX = "unk"

_SCHEMA_ORG_IRIS = ("http://schema.org/", "https://schema.org/")

# schema.org property names, prefixed schema: whatever the source context says
SCHEMA_PROPS = {
    "name", "description", "identifier", "url", "sameAs", "version",
    "dateModified", "datePublished", "dateCreated", "license", "keywords",
    "creator", "author", "publisher", "provider", "funder", "funding",
    "distribution", "spatialCoverage", "temporalCoverage", "variableMeasured",
    "measurementTechnique", "measurementMethod", "citation",
    "isAccessibleForFree", "inLanguage", "includedInDataCatalog",
    "additionalType", "alternateName", "abstract", "encodingFormat",
    "contentUrl", "contentSize", "about", "givenName", "familyName",
    "affiliation", "email", "telephone", "faxNumber", "contactPoint",
    "contactType", "address", "geo", "latitude", "longitude", "box", "polygon",
    "elevation", "additionalProperty", "propertyID", "value", "unitText",
    "unitCode", "minValue", "maxValue", "isBasedOn", "hasPart", "isPartOf",
    "mainEntity", "subjectOf", "creativeWorkStatus", "thumbnailUrl",
    "audience", "size", "conditionsOfAccess", "comment", "roleName",
    "contributor", "locationCreated", "fileFormat", "usageInfo", "usageinfo",
    "potentialAction", "sdDatePublished", "maintainer", "serviceType",
    "termsOfService", "urlTemplate", "httpMethod", "relatedLink", "temporal",
    "spatial", "addressCountry", "addressLocality", "addressRegion",
    "availableLanguage", "caption", "commentCount", "disambiguatingDescription",
    "image", "inDefinedTermSet", "termCode", "parentOrganization",
    "postalCode", "streetAddress", "dayOfWeek", "discussionUrl",
    "hoursAvailable", "interactionStatistic", "interactionType",
    "requiresSubscription", "userInteractionCount",
    # Action / provenance properties
    "agent", "object", "result", "instrument", "participant", "location",
    "startTime", "endTime", "actionStatus", "actionProcess", "error", "target",
    "step", "position", "startDate", "endDate", "category",
}

# schema.org type names
SCHEMA_TYPES = {
    "Person", "Organization", "Place", "GeoShape", "GeoCoordinates",
    "PropertyValue", "CreativeWork", "DataDownload", "DataCatalog",
    "ContactPoint", "MonetaryGrant", "FundingAgency", "ResearchProject",
    "DigitalDocument", "Dataset", "Role", "DefinedTerm", "QuantitativeValue",
    "PostalAddress", "ImageObject", "WebAPI", "SearchAction", "EntryPoint",
    "Action", "Collection", "MediaObject", "SoftwareApplication",
    "SoftwareSourceCode", "Product", "DefinedTermSet", "InteractionCounter",
    "OpeningHoursSpecification",
}

# @type values renamed rather than prefixed
TYPE_MAP = {
    "FundingAgency": "schema:Organization",
    "schema:FundingAgency": "schema:Organization",
    "sc:Dataset": "schema:Dataset",
    "cr:FileObject": "schema:DataDownload",
    "Grant": "schema:MonetaryGrant",
}


def source_vocabulary(ctx):
    """What the source record's own @context says its unprefixed names mean.

    Returns (schema_vocab, terms): *schema_vocab* is True when the context
    makes schema.org the default vocabulary; *terms* maps each name the
    context defines to its IRI. A term definition takes precedence over
    @vocab.
    """
    schema_vocab = False
    terms = {}
    for item in (ctx if isinstance(ctx, list) else [ctx]):
        if isinstance(item, str):
            if item.rstrip("/") + "/" in _SCHEMA_ORG_IRIS:
                schema_vocab = True
        elif isinstance(item, dict):
            vocab = item.get("@vocab")
            if isinstance(vocab, str) and vocab.rstrip("/") + "/" in _SCHEMA_ORG_IRIS:
                schema_vocab = True
            for key, defn in item.items():
                if key.startswith("@") or ":" in key:
                    continue
                iri = defn.get("@id") if isinstance(defn, dict) else defn
                # A value ending in / or # declares a prefix, not a term.
                if isinstance(iri, str) and ":" in iri and not iri.endswith(("/", "#")):
                    terms[key] = iri
    return schema_vocab, terms


def source_prefixes(ctx, exclude=("schema", "dcterms", "dcat", "prov")):
    """The prefix declarations in a source @context (values ending / or #).

    A term alias such as "conformsTo": "dct:conformsTo" is deliberately left
    out: prefix_keys has already applied it, and keeping it would make
    compaction rename dct:conformsTo back to conformsTo, which the CDIF
    schemas do not know.
    """
    out = {}
    for item in (ctx if isinstance(ctx, list) else [ctx]):
        if isinstance(item, dict):
            for k, v in item.items():
                if k.startswith("@") or k in exclude:
                    continue
                if isinstance(v, str) and v.endswith(("/", "#")):
                    out[k] = v
    return out


def prefix_keys(obj, depth=0, assumed=None, unknown=None,
                schema_vocab=False, terms=None):
    """Recursively give every unprefixed property name a prefix.

    *assumed* collects names prefixed schema: only because they match a
    schema.org type name; *unknown* collects names sent to unk:. The
    @context itself is copied unchanged: it declares prefixes and terms and
    is not data.
    """
    if depth > 25:
        return obj
    terms = terms or {}
    if assumed is None:
        assumed = set()
    if unknown is None:
        unknown = set()
    if isinstance(obj, list):
        return [prefix_keys(i, depth + 1, assumed, unknown, schema_vocab, terms)
                for i in obj]
    if not isinstance(obj, dict):
        return obj
    result = {}
    for key, value in obj.items():
        if key == "@context":
            result[key] = value
            continue
        new_key = key
        if not key.startswith("@") and ":" not in key and not key.startswith("http"):
            if key in terms:
                new_key = terms[key]
            elif key in SCHEMA_PROPS:
                new_key = "schema:" + key
            elif key in SCHEMA_TYPES:
                new_key = "schema:" + key
                assumed.add(key)
            elif schema_vocab:
                new_key = "schema:" + key
            else:
                new_key = UNKNOWN_PREFIX + ":" + key
                unknown.add(key)
        result[new_key] = prefix_keys(value, depth + 1, assumed, unknown,
                                      schema_vocab, terms)
    return result


def fix_types(obj, schema_vocab=False, terms=None):
    """Recursively normalize @type to arrays of CURIEs, resolved the same way
    as property names (@type values are vocabulary-relative too)."""
    terms = terms or {}
    if isinstance(obj, list):
        return [fix_types(i, schema_vocab, terms) for i in obj]
    if not isinstance(obj, dict):
        return obj
    if "@type" in obj:
        types = obj["@type"] if isinstance(obj["@type"], list) else [obj["@type"]]
        normalized = []
        for t in types:
            bare = isinstance(t, str) and ":" not in t and not t.startswith("http")
            if t in TYPE_MAP:
                normalized.append(TYPE_MAP[t])
            elif t in terms:
                normalized.append(terms[t])
            elif t in SCHEMA_TYPES:
                normalized.append("schema:" + t)
            elif bare and schema_vocab:
                normalized.append("schema:" + t)
            elif bare:
                normalized.append(UNKNOWN_PREFIX + ":" + t)
            else:
                normalized.append(t)
        obj["@type"] = normalized
    for k, v in obj.items():
        if k != "@type":
            obj[k] = fix_types(v, schema_vocab, terms)
    return obj
