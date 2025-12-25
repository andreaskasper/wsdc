# West Coast Swing Event Report - XSD Schema Documentation

## Overview
This XSD schema validates XML reports for West Coast Swing dance competitions following WSDC standards.

## Key Design Decisions

### ID/IDREF vs String References
- **String-based references** (judge IDs): Judge IDs are scoped locally within each round, allowing reuse (e.g., j1, j2 can appear in multiple rounds)
- **ID/IDREF references** (participants, persons, competitions): These maintain global uniqueness within an event for proper cross-referencing

### Cardinality
- **Report**: Contains 1+ events
- **Event**: Required elements (name, id, dates, participants), optional elements (locations, organisation, persons, competitions)
- **Locations**: 0+ venues or hotels per event
- **Persons**: 0+ persons with roles (organizer, headjudge, scorer, dj, compcoordinator, marshalling)
- **Competitions**: 0+ competitions per event
- **Rounds**: 0+ rounds per competition (prelim, quarter, semi, final)
- **Participants**: 0+ participants per event

### Required vs Optional Fields
- **Participants**: firstname and lastname are required; WSDC ID is optional
- **Addresses**: All sub-elements (street, zip, city, country) are optional
- **Contact Info**: Email and phone are optional for all entities
- **Payment**: PayPal transaction ID is optional

### Enumerations

#### WSDC Categories
- NEW (Newcomer)
- NOV (Novice)
- INT (Intermediate)
- ADV (Advanced)
- ALS (All-Star)
- CHMP (Champion)
- SOPH (Sophisticated)
- MAST (Masters)

#### Person Roles
- organizer
- headjudge
- scorer
- dj
- compcoordinator
- marshalling

#### Judge Roles
- headjudge
- rawscorejudge
- referee

#### Round Types
- prelim (Preliminary)
- quarter (Quarterfinal)
- semi (Semifinal)
- final (Final)

#### Callback Types
- yes
- no
- alt1, alt2, alt3 (Alternates)

#### Competitor Roles
- L (Leader)
- F (Follower)

#### Other Types
- **Tier**: 1-6
- **Country Codes**: ISO 3166-1 alpha-3 (e.g., DEU, USA)
- **Phone Types**: business, mobile, private
- **Location Types**: venue, hotel

### Special Features
- **Round Attributes**: `tablet` and `paper` (boolean) can both be true if mixed judging methods are used
- **Violation Tracking**: Score elements can include violation attributes; separate violation elements list all violations for a result
- **Partner Tracking**: Results can include partner references for couples competitions

## Validation
Use xmllint to validate your XML:
```bash
xmllint --noout --schema event_report.xsd your_file.xml
```

## Version
Schema Version: 1.0
Compatible with Report Version: 0.2+
