from typing import Any, Literal
from uuid import UUID

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from database import engine
from registration import limiter
from registration import router as registration_router

app = FastAPI()


origins = ["http://localhost:3000", "http://127.0.0.1:8000"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.state.limiter = limiter
app.add_exception_handler(
    RateLimitExceeded,
    _rate_limit_exceeded_handler, # type: ignore[arg-type]
)

app.include_router(registration_router)


class Course(BaseModel):
    code: str
    name: str


class CourseUpdate(BaseModel):
    code: str | None = None
    name: str | None = None


class SyncOperationRequest(BaseModel):
    entity_type: Literal["semester", "course", "schedule"]
    sync_id: UUID
    operation: Literal["upsert", "delete"]
    payload: dict[str, Any] | None = None


@app.get("/")
def home():
    return "Hello"


@app.get("/api/courses")
def get_courses():
    with engine.connect() as connection:
        result = connection.execute(text("SELECT id, code, name FROM courses"))

        courses = []

        for row in result:
            courses.append({"id": row.id, "code": row.code, "name": row.name})

        return courses


@app.get("/api/courses/{course_id}")
def get_course(course_id: int):
    with engine.connect() as connection:
        result = connection.execute(
            text("""SELECT id, code, name 
            FROM courses
            WHERE id = :course_id
            """),
            {"course_id": course_id},
        )

        row = result.fetchone()

        if row is None:
            raise HTTPException(status_code=404, detail="Course not found")

        return {"id": row.id, "code": row.code, "name": row.name}


@app.post("/api/courses", status_code=status.HTTP_201_CREATED)
def create_course(course: Course):
    try:
        with engine.begin() as connection:
            result = connection.execute(
                text("""INSERT INTO courses(code, name)
                        VALUES (:code, :name)
                        RETURNING id, code, name;
                """),
                {"code": course.code, "name": course.name},
            )
            new_course = result.mappings().first()
            return new_course

    except IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A course with code '{course.code}' already exists.",
        )


@app.put("/api/courses/{course_id}", status_code=status.HTTP_200_OK)
def replace_course(course_id: int, course: Course):
    try:
        with engine.begin() as connection:
            result = connection.execute(
                text("""UPDATE courses
                SET code = :code,
                    name = :name
                WHERE id = :course_id
                RETURNING id, code, name;
                """),
                {"code": course.code, "name": course.name, "course_id": course_id},
            )
            row = result.mappings().first()

            if row is None:
                raise HTTPException(status_code=404, detail="Course not found")

            return row
    except IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A course with code '{course.code}' already exists.",
        )


@app.patch("/api/courses/{course_id}", status_code=status.HTTP_200_OK)
def patch_course(course_id: int, course_data: CourseUpdate):
    update_data = course_data.model_dump(exclude_unset=True)

    fields_count = len(update_data)

    if fields_count == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields provided for update.",
        )

    set_clauses = []
    for key in update_data.keys():
        set_clauses.append(f"{key} = :{key}")

    set_clause_str = ", ".join(set_clauses)

    sql_query = f"""
        UPDATE courses
        SET {set_clause_str}
        WHERE id = :course_id
        RETURNING id, code, name;
    """

    update_data["course_id"] = course_id

    try:
        with engine.begin() as connection:
            result = connection.execute(text(sql_query), update_data)
            row = result.mappings().first()

            if row is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Course not found"
                )

            print(
                f"Successfully modified {fields_count} field(s) for course ID {course_id}"
            )

            return row

    except IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A course with that code already exists.",
        )


@app.delete("/api/courses/{course_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_course(course_id: int):
    try:
        with engine.begin() as connection:
            result = connection.execute(
                text("""DELETE FROM courses 
                        WHERE id = :course_id;
                """),
                {"course_id": course_id},
            )

            if result.rowcount == 0:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Course not found"
                )

            return

    except IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete this course because it is currently linked to other records (e.g., classes or enrollments).",
        )


def required_payload_value(payload: dict, key: str):
    if key not in payload or payload[key] is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Missing required payload field: {key}",
        )
    return payload[key]


def optional_payload_uuid(payload: dict, key: str) -> str | None:
    value = payload.get(key)

    if value is None:
        return None

    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid UUID for payload field: {key}",
        )


@app.post("/api/sync")
def sync_operation(request: SyncOperationRequest):
    payload = request.payload or {}
    sync_id = str(request.sync_id)

    if request.operation == "upsert" and request.payload is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Upsert operations require a payload.",
        )

    # Prevent the payload from referring to a different entity.
    payload_sync_id = payload.get("sync_id")

    if payload_sync_id is not None:
        try:
            if UUID(str(payload_sync_id)) != request.sync_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Payload sync_id does not match request sync_id.",
                )
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid payload sync_id.",
            )

    try:
        with engine.begin() as connection:
            # ----------------------------------------
            # DELETE OPERATIONS
            # ----------------------------------------
            if request.operation == "delete":
                table_names = {
                    "semester": "semesters",
                    "course": "courses",
                    "schedule": "schedules",
                }

                table = table_names[request.entity_type]

                result = connection.execute(
                    text(
                        f"""
                        DELETE FROM {table}
                        WHERE sync_id = CAST(:sync_id AS UUID)
                        RETURNING id
                        """
                    ),
                    {"sync_id": sync_id},
                )

                deleted_row = result.first()

                # Deleting an already-absent entity counts as success.
                # This makes retries safe after an uncertain response.
                return {
                    "status": "synced",
                    "entity_type": request.entity_type,
                    "sync_id": sync_id,
                    "operation": "delete",
                    "found": deleted_row is not None,
                }

            # ----------------------------------------
            # SEMESTER UPSERT
            # ----------------------------------------
            if request.entity_type == "semester":
                name = required_payload_value(payload, "name")
                start_date = required_payload_value(payload, "start_date")
                end_date = required_payload_value(payload, "end_date")

                result = connection.execute(
                    text(
                        """
                        INSERT INTO semesters (
                            sync_id,
                            name,
                            start_date,
                            end_date
                        )
                        VALUES (
                            CAST(:sync_id AS UUID),
                            :name,
                            CAST(:start_date AS TIMESTAMP),
                            CAST(:end_date AS TIMESTAMP)
                        )
                        ON CONFLICT (sync_id)
                        DO UPDATE SET
                            name = EXCLUDED.name,
                            start_date = EXCLUDED.start_date,
                            end_date = EXCLUDED.end_date
                        RETURNING id
                        """
                    ),
                    {
                        "sync_id": sync_id,
                        "name": name,
                        "start_date": start_date,
                        "end_date": end_date,
                    },
                )

            # ----------------------------------------
            # COURSE UPSERT
            # ----------------------------------------
            elif request.entity_type == "course":
                code = required_payload_value(payload, "code")
                name = required_payload_value(payload, "name")

                semester_sync_id = optional_payload_uuid(payload, "semester_sync_id")

                semester_id = None

                if semester_sync_id is not None:
                    semester_result = connection.execute(
                        text(
                            """
                            SELECT id
                            FROM semesters
                            WHERE sync_id = CAST(:sync_id AS UUID)
                            """
                        ),
                        {"sync_id": semester_sync_id},
                    )

                    semester_row = semester_result.first()

                    if semester_row is None:
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail=(
                                "Semester has not synced yet. "
                                "Retry the course after its semester syncs."
                            ),
                        )

                    semester_id = semester_row.id

                result = connection.execute(
                    text(
                        """
                        INSERT INTO courses (
                            sync_id,
                            code,
                            name,
                            semester_id
                        )
                        VALUES (
                            CAST(:sync_id AS UUID),
                            :code,
                            :name,
                            :semester_id
                        )
                        ON CONFLICT (sync_id)
                        DO UPDATE SET
                            code = EXCLUDED.code,
                            name = EXCLUDED.name,
                            semester_id = EXCLUDED.semester_id
                        RETURNING id
                        """
                    ),
                    {
                        "sync_id": sync_id,
                        "code": code,
                        "name": name,
                        "semester_id": semester_id,
                    },
                )

            # ----------------------------------------
            # SCHEDULE UPSERT
            # ----------------------------------------
            else:
                title = required_payload_value(payload, "title")
                day_of_week = required_payload_value(payload, "day_of_week")
                start_time = required_payload_value(payload, "start_time")
                end_time = required_payload_value(payload, "end_time")
                schedule_type = required_payload_value(payload, "type")

                course_sync_id = optional_payload_uuid(payload, "course_sync_id")

                course_id = None

                if course_sync_id is not None:
                    course_result = connection.execute(
                        text(
                            """
                            SELECT id
                            FROM courses
                            WHERE sync_id = CAST(:sync_id AS UUID)
                            """
                        ),
                        {"sync_id": course_sync_id},
                    )

                    course_row = course_result.first()

                    if course_row is None:
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail=(
                                "Course has not synced yet. "
                                "Retry the schedule after its course syncs."
                            ),
                        )

                    course_id = course_row.id

                result = connection.execute(
                    text(
                        """
                        INSERT INTO schedules (
                            sync_id,
                            course_id,
                            title,
                            description,
                            day_of_week,
                            start_time,
                            end_time,
                            location,
                            type
                        )
                        VALUES (
                            CAST(:sync_id AS UUID),
                            :course_id,
                            :title,
                            :description,
                            :day_of_week,
                            :start_time,
                            :end_time,
                            :location,
                            :type
                        )
                        ON CONFLICT (sync_id)
                        DO UPDATE SET
                            course_id = EXCLUDED.course_id,
                            title = EXCLUDED.title,
                            description = EXCLUDED.description,
                            day_of_week = EXCLUDED.day_of_week,
                            start_time = EXCLUDED.start_time,
                            end_time = EXCLUDED.end_time,
                            location = EXCLUDED.location,
                            type = EXCLUDED.type
                        RETURNING id
                        """
                    ),
                    {
                        "sync_id": sync_id,
                        "course_id": course_id,
                        "title": title,
                        "description": payload.get("description"),
                        "day_of_week": day_of_week,
                        "start_time": start_time,
                        "end_time": end_time,
                        "location": payload.get("location"),
                        "type": schedule_type,
                    },
                )

            row = result.mappings().first()

            if row is None:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Database did not return the synchronized record.",
                )

            return {
                "status": "synced",
                "entity_type": request.entity_type,
                "sync_id": sync_id,
                "operation": "upsert",
                "server_id": row["id"],
            }

    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "The sync operation conflicts with existing data "
                "or a database relationship constraint."
            ),
        ) from exc
