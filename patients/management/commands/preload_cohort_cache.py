"""
Management command: preload_cohort_cache

Creates and populates simulation.sim_cache_* tables with all March 13 data
for the cohort patients. Run ONCE after migrate; persists on RDS across restarts.

Usage:
    python manage.py preload_cohort_cache

Re-run only if cohort.py changes (cohort patients are added/removed).
Safe to re-run: truncates and repopulates each cache table.
"""

from django.core.management.base import BaseCommand
from django.db import connection

from patients.cohort import PATIENT_STAYS

COHORT_STAY_IDS = [stay_id for _, stay_id, _ in PATIENT_STAYS]


class Command(BaseCommand):
    help = (
        "Preload March 13 cohort data from MIMIC into simulation.sim_cache_* tables. "
        "Run once after migrate. Re-run only if cohort.py changes."
    )

    def handle(self, *args, **options):
        self.stdout.write("=== preload_cohort_cache ===")
        self.stdout.write(f"Cohort size: {len(COHORT_STAY_IDS)} patients")

        self.stdout.write("\n[1/6] Creating cache tables...")
        self._create_tables()
        self.stdout.write("      Done.")

        self.stdout.write("\n[2/6] Populating sim_cache_icustays...")
        n = self._populate_icustays()
        self.stdout.write(f"      {n} rows inserted.")

        self.stdout.write("\n[3/6] Populating sim_cache_vitalsign_hourly...")
        n = self._populate_vitalsign()
        self.stdout.write(f"      {n} rows inserted.")

        self.stdout.write("\n[4/6] Populating sim_cache_procedures...")
        n = self._populate_procedures()
        self.stdout.write(f"      {n} rows inserted.")

        self.stdout.write("\n[5/6] Populating sim_cache_chemistry_hourly...")
        n = self._populate_chemistry()
        self.stdout.write(f"      {n} rows inserted.")

        self.stdout.write("\n[6/6] Populating sim_cache_coagulation_hourly...")
        n = self._populate_coagulation()
        self.stdout.write(f"      {n} rows inserted.")

        self.stdout.write("\n[optional] Populating sim_cache_sofa_hourly...")
        n = self._populate_sofa()
        if n is None:
            self.stdout.write("      Skipped (mimiciv_derived.sofa_hourly not found).")
        else:
            self.stdout.write(f"      {n} rows inserted.")

        self.stdout.write(self.style.SUCCESS("\n=== Cache preload complete. ==="))
        self.stdout.write(
            "Each advance_hour() call will now read from cache tables (~60 rows) "
            "instead of scanning millions of MIMIC rows."
        )

    # -------------------------------------------------------------------------
    # Table creation
    # -------------------------------------------------------------------------

    def _create_tables(self):
        ddl_statements = [
            # icustays cache — one row per patient, includes intime_hour for fast admit lookup
            """
            CREATE TABLE IF NOT EXISTS simulation.sim_cache_icustays (
                subject_id     INTEGER,
                anchor_age     SMALLINT,
                gender         VARCHAR(1),
                race           VARCHAR(80),
                hadm_id        INTEGER,
                stay_id        INTEGER PRIMARY KEY,
                first_careunit VARCHAR(255),
                last_careunit  VARCHAR(255),
                intime         TIMESTAMP,
                outtime        TIMESTAMP,
                los            FLOAT,
                intime_hour    SMALLINT
            )
            """,
            # vitalsign — pre-aggregated AVG per (stay_id, hour), charttime normalized
            """
            CREATE TABLE IF NOT EXISTS simulation.sim_cache_vitalsign_hourly (
                id               SERIAL PRIMARY KEY,
                subject_id       INTEGER,
                stay_id          INTEGER,
                charttime_hour   TIMESTAMP,
                heart_rate       FLOAT,
                sbp              FLOAT,
                dbp              FLOAT,
                mbp              FLOAT,
                sbp_ni           FLOAT,
                dbp_ni           FLOAT,
                mbp_ni           FLOAT,
                resp_rate        FLOAT,
                temperature      NUMERIC(5,2),
                temperature_site TEXT,
                spo2             FLOAT,
                glucose          FLOAT
            )
            """,
            "CREATE INDEX IF NOT EXISTS sim_cache_vs_idx ON simulation.sim_cache_vitalsign_hourly (stay_id, charttime_hour)",
            # procedures — one row per event, charttime normalized
            """
            CREATE TABLE IF NOT EXISTS simulation.sim_cache_procedures (
                id                      SERIAL PRIMARY KEY,
                subject_id              INTEGER,
                stay_id                 INTEGER,
                charttime_hour          TIMESTAMP,
                charttime               TIMESTAMP,
                caregiver_id            INTEGER,
                itemid                  INTEGER,
                item_label              VARCHAR(100),
                item_unitname           VARCHAR(50),
                item_lownormalvalue     FLOAT,
                item_highnormalvalue    FLOAT,
                value                   FLOAT,
                valueuom                VARCHAR(20),
                location                VARCHAR(100),
                locationcategory        VARCHAR(50),
                orderid                 INTEGER,
                linkorderid             INTEGER,
                ordercategoryname       VARCHAR(50),
                ordercategorydescription VARCHAR(30),
                patientweight           FLOAT,
                isopenbag               SMALLINT,
                continueinnextdept      SMALLINT,
                statusdescription       VARCHAR(20),
                originalamount          FLOAT,
                originalrate            FLOAT
            )
            """,
            "CREATE INDEX IF NOT EXISTS sim_cache_proc_idx ON simulation.sim_cache_procedures (stay_id, charttime_hour)",
            # chemistry — pre-aggregated per (stay_id, hour)
            """
            CREATE TABLE IF NOT EXISTS simulation.sim_cache_chemistry_hourly (
                id             SERIAL PRIMARY KEY,
                subject_id     INTEGER,
                stay_id        INTEGER,
                charttime_hour TIMESTAMP,
                bicarbonate    FLOAT,
                calcium        FLOAT,
                sodium         FLOAT,
                potassium      FLOAT
            )
            """,
            "CREATE INDEX IF NOT EXISTS sim_cache_chem_idx ON simulation.sim_cache_chemistry_hourly (stay_id, charttime_hour)",
            # coagulation — pre-aggregated per (stay_id, hour)
            """
            CREATE TABLE IF NOT EXISTS simulation.sim_cache_coagulation_hourly (
                id             SERIAL PRIMARY KEY,
                subject_id     INTEGER,
                stay_id        INTEGER,
                charttime_hour TIMESTAMP,
                d_dimer        FLOAT,
                fibrinogen     FLOAT,
                thrombin       FLOAT,
                inr            FLOAT,
                pt             FLOAT,
                ptt            FLOAT
            )
            """,
            "CREATE INDEX IF NOT EXISTS sim_cache_coag_idx ON simulation.sim_cache_coagulation_hourly (stay_id, charttime_hour)",
            # sofa — per (stay_id, hour)
            """
            CREATE TABLE IF NOT EXISTS simulation.sim_cache_sofa_hourly (
                id             SERIAL PRIMARY KEY,
                subject_id     INTEGER,
                stay_id        INTEGER,
                charttime_hour TIMESTAMP,
                sofa_24hours   INTEGER,
                respiration    INTEGER,
                coagulation    INTEGER,
                liver          INTEGER,
                cardiovascular INTEGER,
                cns            INTEGER,
                renal          INTEGER
            )
            """,
            "CREATE INDEX IF NOT EXISTS sim_cache_sofa_idx ON simulation.sim_cache_sofa_hourly (stay_id, charttime_hour)",
        ]
        with connection.cursor() as cursor:
            for stmt in ddl_statements:
                cursor.execute(stmt)

    # -------------------------------------------------------------------------
    # Population helpers
    # -------------------------------------------------------------------------

    def _populate_icustays(self) -> int:
        sql = """
            TRUNCATE simulation.sim_cache_icustays;

            INSERT INTO simulation.sim_cache_icustays
                (subject_id, anchor_age, gender, race, hadm_id, stay_id,
                 first_careunit, last_careunit, intime, outtime, los, intime_hour)
            SELECT
                i.subject_id,
                a.anchor_age,
                id.gender,
                id.race,
                i.hadm_id,
                i.stay_id,
                i.first_careunit,
                i.last_careunit,
                i.intime,
                i.outtime,
                i.los,
                EXTRACT(HOUR FROM i.intime)::smallint AS intime_hour
            FROM mimiciv_icu.icustays i
            JOIN mimiciv_derived.age a
                ON a.subject_id = i.subject_id AND a.hadm_id = i.hadm_id
            JOIN mimiciv_derived.icustay_detail id
                ON id.stay_id = i.stay_id
            WHERE i.stay_id = ANY(%s)
              AND EXTRACT(MONTH FROM i.intime) = 3
              AND EXTRACT(DAY   FROM i.intime) = 13
        """
        with connection.cursor() as cursor:
            cursor.execute(sql, [COHORT_STAY_IDS])
            cursor.execute("SELECT COUNT(*) FROM simulation.sim_cache_icustays")
            return cursor.fetchone()[0]

    def _populate_vitalsign(self) -> int:
        sql = """
            TRUNCATE simulation.sim_cache_vitalsign_hourly;

            INSERT INTO simulation.sim_cache_vitalsign_hourly
                (subject_id, stay_id, charttime_hour,
                 heart_rate, sbp, dbp, mbp, sbp_ni, dbp_ni, mbp_ni,
                 resp_rate, temperature, temperature_site, spo2, glucose)
            SELECT
                v.subject_id,
                v.stay_id,
                MAKE_TIMESTAMP(2025, 3, 13, EXTRACT(HOUR FROM v.charttime)::int, 0, 0) AS charttime_hour,
                AVG(v.heart_rate)     FILTER (WHERE v.heart_rate IS NOT NULL)    AS heart_rate,
                AVG(v.sbp)            FILTER (WHERE v.sbp IS NOT NULL)           AS sbp,
                AVG(v.dbp)            FILTER (WHERE v.dbp IS NOT NULL)           AS dbp,
                AVG(v.mbp)            FILTER (WHERE v.mbp IS NOT NULL)           AS mbp,
                AVG(v.sbp_ni)         FILTER (WHERE v.sbp_ni IS NOT NULL)        AS sbp_ni,
                AVG(v.dbp_ni)         FILTER (WHERE v.dbp_ni IS NOT NULL)        AS dbp_ni,
                AVG(v.mbp_ni)         FILTER (WHERE v.mbp_ni IS NOT NULL)        AS mbp_ni,
                AVG(v.resp_rate)      FILTER (WHERE v.resp_rate IS NOT NULL)     AS resp_rate,
                AVG(v.temperature)    FILTER (WHERE v.temperature IS NOT NULL)   AS temperature,
                (ARRAY_AGG(v.temperature_site ORDER BY v.charttime)
                    FILTER (WHERE v.temperature_site IS NOT NULL))[1]            AS temperature_site,
                AVG(v.spo2)           FILTER (WHERE v.spo2 IS NOT NULL)          AS spo2,
                AVG(v.glucose)        FILTER (WHERE v.glucose IS NOT NULL)       AS glucose
            FROM mimiciv_derived.vitalsign v
            WHERE v.stay_id = ANY(%s)
              AND EXTRACT(MONTH FROM v.charttime) = 3
              AND EXTRACT(DAY   FROM v.charttime) = 13
            GROUP BY v.subject_id, v.stay_id, EXTRACT(HOUR FROM v.charttime)::int
        """
        with connection.cursor() as cursor:
            cursor.execute(sql, [COHORT_STAY_IDS])
            cursor.execute("SELECT COUNT(*) FROM simulation.sim_cache_vitalsign_hourly")
            return cursor.fetchone()[0]

    def _populate_procedures(self) -> int:
        sql = """
            TRUNCATE simulation.sim_cache_procedures;

            INSERT INTO simulation.sim_cache_procedures
                (subject_id, stay_id, charttime_hour, charttime, caregiver_id,
                 itemid, item_label, item_unitname, item_lownormalvalue, item_highnormalvalue,
                 value, valueuom, location, locationcategory, orderid, linkorderid,
                 ordercategoryname, ordercategorydescription, patientweight,
                 isopenbag, continueinnextdept, statusdescription,
                 originalamount, originalrate)
            SELECT
                p.subject_id,
                p.stay_id,
                MAKE_TIMESTAMP(2025, 3, 13,
                    EXTRACT(HOUR FROM (p.storetime + INTERVAL '30 minutes'))::int, 0, 0) AS charttime_hour,
                p.storetime       AS charttime,
                p.caregiver_id,
                p.itemid,
                di.label          AS item_label,
                di.unitname       AS item_unitname,
                di.lownormalvalue AS item_lownormalvalue,
                di.highnormalvalue AS item_highnormalvalue,
                p.value,
                p.valueuom,
                p.location,
                p.locationcategory,
                p.orderid,
                p.linkorderid,
                p.ordercategoryname,
                p.ordercategorydescription,
                p.patientweight,
                p.isopenbag,
                p.continueinnextdept,
                p.statusdescription,
                p.originalamount,
                p.originalrate
            FROM mimiciv_icu.procedureevents p
            LEFT JOIN mimiciv_icu.d_items di ON di.itemid = p.itemid
            WHERE p.stay_id = ANY(%s)
              AND p.storetime IS NOT NULL
              AND EXTRACT(MONTH FROM p.storetime) = 3
              AND EXTRACT(DAY   FROM p.storetime) = 13
        """
        with connection.cursor() as cursor:
            cursor.execute(sql, [COHORT_STAY_IDS])
            cursor.execute("SELECT COUNT(*) FROM simulation.sim_cache_procedures")
            return cursor.fetchone()[0]

    def _populate_chemistry(self) -> int:
        sql = """
            TRUNCATE simulation.sim_cache_chemistry_hourly;

            INSERT INTO simulation.sim_cache_chemistry_hourly
                (subject_id, stay_id, charttime_hour,
                 bicarbonate, calcium, sodium, potassium)
            SELECT
                ch.subject_id,
                sp.stay_id,
                MAKE_TIMESTAMP(2025, 3, 13,
                    EXTRACT(HOUR FROM (ch.charttime + INTERVAL '30 minutes'))::int, 0, 0) AS charttime_hour,
                MIN(ch.bicarbonate) FILTER (WHERE ch.bicarbonate IS NOT NULL) AS bicarbonate,
                AVG(ch.calcium)     FILTER (WHERE ch.calcium IS NOT NULL)     AS calcium,
                AVG(ch.sodium)      FILTER (WHERE ch.sodium IS NOT NULL)      AS sodium,
                MAX(ch.potassium)   FILTER (WHERE ch.potassium IS NOT NULL)   AS potassium
            FROM mimiciv_derived.chemistry ch
            JOIN simulation.sim_cache_icustays sp ON sp.subject_id = ch.subject_id
            WHERE sp.stay_id = ANY(%s)
              AND EXTRACT(MONTH FROM ch.charttime) = 3
              AND EXTRACT(DAY   FROM ch.charttime) = 13
            GROUP BY ch.subject_id, sp.stay_id,
                     EXTRACT(HOUR FROM (ch.charttime + INTERVAL '30 minutes'))::int
        """
        with connection.cursor() as cursor:
            cursor.execute(sql, [COHORT_STAY_IDS])
            cursor.execute("SELECT COUNT(*) FROM simulation.sim_cache_chemistry_hourly")
            return cursor.fetchone()[0]

    def _populate_coagulation(self) -> int:
        sql = """
            TRUNCATE simulation.sim_cache_coagulation_hourly;

            INSERT INTO simulation.sim_cache_coagulation_hourly
                (subject_id, stay_id, charttime_hour,
                 d_dimer, fibrinogen, thrombin, inr, pt, ptt)
            SELECT
                co.subject_id,
                sp.stay_id,
                MAKE_TIMESTAMP(2025, 3, 13,
                    EXTRACT(HOUR FROM (co.charttime + INTERVAL '30 minutes'))::int, 0, 0) AS charttime_hour,
                (ARRAY_AGG(co.inr      ORDER BY co.charttime DESC)
                    FILTER (WHERE co.inr IS NOT NULL))[1]      AS inr,
                (ARRAY_AGG(co.pt       ORDER BY co.charttime DESC)
                    FILTER (WHERE co.pt IS NOT NULL))[1]       AS pt,
                (ARRAY_AGG(co.ptt      ORDER BY co.charttime DESC)
                    FILTER (WHERE co.ptt IS NOT NULL))[1]      AS ptt,
                (ARRAY_AGG(co.thrombin ORDER BY co.charttime DESC)
                    FILTER (WHERE co.thrombin IS NOT NULL))[1] AS thrombin,
                MAX(co.d_dimer)    FILTER (WHERE co.d_dimer IS NOT NULL)    AS d_dimer,
                MIN(co.fibrinogen) FILTER (WHERE co.fibrinogen IS NOT NULL) AS fibrinogen
            FROM mimiciv_derived.coagulation co
            JOIN simulation.sim_cache_icustays sp ON sp.subject_id = co.subject_id
            WHERE sp.stay_id = ANY(%s)
              AND EXTRACT(MONTH FROM co.charttime) = 3
              AND EXTRACT(DAY   FROM co.charttime) = 13
            GROUP BY co.subject_id, sp.stay_id,
                     EXTRACT(HOUR FROM (co.charttime + INTERVAL '30 minutes'))::int
        """
        with connection.cursor() as cursor:
            cursor.execute(sql, [COHORT_STAY_IDS])
            cursor.execute("SELECT COUNT(*) FROM simulation.sim_cache_coagulation_hourly")
            return cursor.fetchone()[0]

    def _populate_sofa(self):
        """Populate sofa cache — skips gracefully if source table doesn't exist."""
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT to_regclass('mimiciv_derived.sofa_hourly') IS NOT NULL"
            )
            if not cursor.fetchone()[0]:
                return None

        sql = """
            TRUNCATE simulation.sim_cache_sofa_hourly;

            INSERT INTO simulation.sim_cache_sofa_hourly
                (subject_id, stay_id, charttime_hour,
                 sofa_24hours, respiration, coagulation, liver,
                 cardiovascular, cns, renal)
            SELECT
                sp.subject_id,
                s.stay_id,
                MAKE_TIMESTAMP(2025, 3, 13, EXTRACT(HOUR FROM s.starttime)::int, 0, 0) AS charttime_hour,
                s.sofa_24hours,
                s.respiration,
                s.coagulation,
                s.liver,
                s.cardiovascular,
                s.cns,
                s.renal
            FROM mimiciv_derived.sofa_hourly s
            JOIN simulation.sim_cache_icustays sp ON sp.stay_id = s.stay_id
            WHERE s.stay_id = ANY(%s)
              AND EXTRACT(MONTH FROM s.starttime) = 3
              AND EXTRACT(DAY   FROM s.starttime) = 13
        """
        with connection.cursor() as cursor:
            cursor.execute(sql, [COHORT_STAY_IDS])
            cursor.execute("SELECT COUNT(*) FROM simulation.sim_cache_sofa_hourly")
            return cursor.fetchone()[0]
