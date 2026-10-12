-- One row per transaction + player: each player movement ("leg") in Yahoo's
-- transaction log. An add/drop is two legs, one per player. Flatten, type, rename.
--
-- Shape quirk handled here: a leg's transaction_data is a LIST for an add but a
-- single OBJECT for a drop. Recursive descent inside it (`transaction_data..type`)
-- reads both the same way, so neither shape needs a special case.
--
-- faab_bid is reported on the transaction, so it is repeated on every leg —
-- the dropped player in an add/drop carries the same bid as the added one. It is
-- kept as transaction_faab_bid here, unattributed; fct_transaction assigns it to
-- the add leg, which is what keeps bids from being double-counted.
--
-- Commissioner actions ('commish') carry no players and so produce no rows.

with source as (

    select * from {{ source('raw_yahoo', 'transactions') }}

),

transactions as (

    select
        payload,
        unnest(json_extract(payload, '$.data..transaction')) as tx
    from source

),

legs as (

    select
        payload,
        tx,
        unnest(json_extract(tx, '$..player')) as player
    from transactions

),

renamed as (

    select
        -- Grain -----------------------------------------------------------------
        regexp_extract(json_extract_string(tx, '$[*].transaction_key')[1],
                       '^(.*)\.tr\.\d+$', 1)                                  as league_key,
        json_extract_string(tx, '$[*].transaction_key')[1]                    as transaction_key,
        json_extract_string(player, '$[*][*].player_key')[1]                  as player_key,

        -- Transaction -------------------------------------------------------------
        cast(json_extract_string(tx, '$[*].transaction_id')[1] as integer)    as transaction_id,
        json_extract_string(tx, '$[*].type')[1]                               as transaction_type,
        json_extract_string(tx, '$[*].status')[1]                             as transaction_status,
        to_timestamp(cast(json_extract_string(tx, '$[*].timestamp')[1] as bigint))
                                                                              as transacted_at,
        cast(json_extract_string(tx, '$[*].faab_bid')[1] as integer)          as transaction_faab_bid,

        -- Player ------------------------------------------------------------------
        json_extract_string(player, '$[*][*].name.full')[1]                   as player_name,
        json_extract_string(player, '$[*][*].display_position')[1]            as display_position,

        -- This leg ------------------------------------------------------------------
        json_extract_string(player, '$[*].transaction_data..type')[1]         as leg_type,
        -- 'freeagents', 'waivers' or 'team'
        json_extract_string(player, '$[*].transaction_data..source_type')[1]  as source_type,
        json_extract_string(player, '$[*].transaction_data..destination_type')[1]
                                                                              as destination_type,
        json_extract_string(player, '$[*].transaction_data..source_team_key')[1]
                                                                              as source_team_key,
        json_extract_string(player, '$[*].transaction_data..destination_team_key')[1]
                                                                              as destination_team_key,

        cast(payload ->> '$._meta.fetched_at' as timestamptz)                 as fetched_at

    from legs

)

select * from renamed
