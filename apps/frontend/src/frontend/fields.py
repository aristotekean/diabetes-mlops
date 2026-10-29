"""Form definition for the 44 inputs of the inference contract.

The frontend talks to the API only, so it keeps its own copy of the field list
(see docs/data-contract.md). Option lists come from the full dataset; the API
remains the source of truth and rejects anything outside its contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

# Shown for categorical fields whose value can be missing; sent as JSON null.
UNKNOWN = "(desconocido)"

Kind = Literal["select", "number", "text"]


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    kind: Kind
    options: tuple[str, ...] = ()
    nullable: bool = False
    min_value: int = 0
    max_value: int = 0
    default: Any = None
    help: str | None = None


def _select(name: str, label: str, options: tuple[str, ...], *, nullable: bool = False,
            default: str | None = None, help: str | None = None) -> Field:
    return Field(name, label, "select", options, nullable,
                 default=default if default is not None else options[0], help=help)


def _number(name: str, label: str, low: int, high: int, default: int,
            help: str | None = None) -> Field:
    return Field(name, label, "number", min_value=low, max_value=high, default=default, help=help)


def _diagnosis(name: str, label: str, default: str | None) -> Field:
    return Field(name, label, "text", nullable=True, default=default,
                 help="Código ICD-9, p. ej. 428, 250.83 o V45. Vacío = desconocido.")


AGE_BUCKETS = tuple(f"[{low}-{low + 10})" for low in range(0, 100, 10))
MEDICATION_DOSES = ("No", "Steady", "Up", "Down")
ADMISSION_TYPE_IDS = ("1", "2", "3", "4", "5", "6", "7", "8")
DISCHARGE_DISPOSITION_IDS = tuple(str(code) for code in range(1, 29) if code not in (21, 26))
ADMISSION_SOURCE_IDS = tuple(
    str(code) for code in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 14, 17, 20, 22, 25)
)
PAYER_CODES = (
    "MC", "HM", "SP", "BC", "MD", "CP", "UN", "CM", "OG", "PO", "DM", "CH", "WC", "OT",
    "MP", "SI", "FR",
)
RACES = ("Caucasian", "AfricanAmerican", "Hispanic", "Asian", "Other")
MEDICAL_SPECIALTIES = (
    "InternalMedicine", "Emergency/Trauma", "Family/GeneralPractice", "Cardiology",
    "Surgery-General", "Nephrology", "Orthopedics", "Orthopedics-Reconstructive",
    "Radiologist", "Pulmonology", "Psychiatry", "Urology", "ObstetricsandGynecology",
    "Surgery-Cardiovascular/Thoracic", "Gastroenterology", "Surgery-Vascular", "Surgery-Neuro",
    "PhysicalMedicineandRehabilitation", "Oncology", "Pediatrics", "Hematology/Oncology",
    "Neurology", "Pediatrics-Endocrinology", "Otolaryngology", "Endocrinology",
    "Surgery-Thoracic", "Psychology", "Podiatry", "Surgery-Cardiovascular",
    "Pediatrics-CriticalCare", "Hematology", "Gynecology", "Hospitalist", "Radiology",
    "Surgeon", "Surgery-Plastic", "Osteopath", "Ophthalmology", "InfectiousDiseases",
    "SurgicalSpecialty", "Obsterics&Gynecology-GynecologicOnco", "Pediatrics-Pulmonology",
    "Anesthesiology-Pediatric", "Obstetrics", "Rheumatology", "Pathology", "Anesthesiology",
    "OutreachServices", "Surgery-Colon&Rectal", "Surgery-Maxillofacial", "PhysicianNotFound",
    "Pediatrics-Neurology", "Surgery-Pediatric", "Endocrinology-Metabolism",
    "Psychiatry-Child/Adolescent", "AllergyandImmunology", "Cardiology-Pediatric", "DCPTEAM",
    "Pediatrics-Hematology-Oncology", "Dentistry", "Pediatrics-EmergencyMedicine",
    "Pediatrics-AllergyandImmunology", "Resident", "Surgery-PlasticwithinHeadandNeck",
    "Pediatrics-InfectiousDiseases", "Psychiatry-Addictive", "Proctology", "Dermatology",
    "SportsMedicine", "Speech", "Perinatology", "Neurophysiology",
)
MEDICATIONS = (
    "metformin", "repaglinide", "nateglinide", "chlorpropamide", "glimepiride",
    "acetohexamide", "glipizide", "glyburide", "tolbutamide", "pioglitazone",
    "rosiglitazone", "acarbose", "miglitol", "troglitazone", "tolazamide", "insulin",
    "glyburide-metformin", "glipizide-metformin", "glimepiride-pioglitazone",
    "metformin-rosiglitazone", "metformin-pioglitazone",
)

SECTIONS: tuple[tuple[str, tuple[Field, ...]], ...] = (
    ("Paciente", (
        _select("race", "Raza", RACES, nullable=True),
        _select("gender", "Género", ("Female", "Male", "Unknown/Invalid")),
        _select("age", "Rango de edad", AGE_BUCKETS, default="[60-70)"),
        _select("payer_code", "Código de pagador", PAYER_CODES, nullable=True, default="MC"),
    )),
    ("Ingreso", (
        _select("admission_type_id", "Tipo de ingreso (ID)", ADMISSION_TYPE_IDS,
                help="1 = emergencia, 2 = urgencia, 3 = electivo."),
        _select("admission_source_id", "Origen del ingreso (ID)", ADMISSION_SOURCE_IDS,
                default="7", help="7 = sala de emergencias, 1 = remisión médica."),
        _select("discharge_disposition_id", "Destino al alta (ID)", DISCHARGE_DISPOSITION_IDS,
                help="1 = alta a casa. 11, 13, 14, 19 y 20 = fallecido u hospicio."),
        _select("medical_specialty", "Especialidad que admite", MEDICAL_SPECIALTIES,
                nullable=True),
        _number("time_in_hospital", "Días de estancia", 1, 14, 4),
    )),
    ("Uso de servicios el año anterior", (
        _number("number_outpatient", "Visitas ambulatorias", 0, 100, 0),
        _number("number_emergency", "Visitas a emergencias", 0, 100, 0),
        _number("number_inpatient", "Hospitalizaciones", 0, 100, 0),
    )),
    ("Procedimientos y diagnósticos", (
        _number("num_lab_procedures", "Pruebas de laboratorio", 0, 200, 44),
        _number("num_procedures", "Procedimientos", 0, 20, 1),
        _number("num_medications", "Medicamentos administrados", 0, 150, 15),
        _number("number_diagnoses", "Número de diagnósticos", 0, 30, 8),
        _diagnosis("diag_1", "Diagnóstico principal", "428"),
        _diagnosis("diag_2", "Diagnóstico secundario", "250"),
        _diagnosis("diag_3", "Diagnóstico adicional", None),
    )),
    ("Laboratorio", (
        _select("max_glu_serum", "Glucosa sérica máxima", ("None", "Norm", ">200", ">300")),
        _select("A1Cresult", "Resultado HbA1c", ("None", "Norm", ">7", ">8")),
    )),
    ("Medicamentos para la diabetes", tuple(
        _select(name, name, MEDICATION_DOSES) for name in MEDICATIONS
    )),
    ("Tratamiento", (
        _select("change", "Cambio de medicación", ("No", "Ch")),
        _select("diabetesMed", "Recibe medicación para diabetes", ("Yes", "No")),
    )),
)

FIELDS: tuple[Field, ...] = tuple(field for _, fields in SECTIONS for field in fields)
FIELDS_BY_NAME = {field.name: field for field in FIELDS}


def default_values() -> dict[str, Any]:
    return {field.name: field.default for field in FIELDS}


def to_widget_value(field: Field, value: Any) -> Any:
    """Map an API value (``None`` for missing) to what the widget displays."""
    if field.kind == "select":
        return UNKNOWN if value is None else value
    if field.kind == "text":
        return "" if value is None else str(value)
    return int(value)


def from_widget_value(field: Field, value: Any) -> Any:
    """Map a widget value back to the JSON value the API expects."""
    if field.kind == "select":
        return None if value == UNKNOWN else value
    if field.kind == "text":
        text = str(value).strip()
        return text or None
    return int(value)


def widget_options(field: Field) -> list[str]:
    options = list(field.options)
    return [*options, UNKNOWN] if field.nullable else options
