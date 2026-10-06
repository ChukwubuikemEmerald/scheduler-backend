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
    _rate_limit_exceeded_handler,
)

app.include_router(registration_router)


class Course(BaseModel):
    code: str
    name: str


class CourseUpdate(BaseModel):
    code: str | None = None
    name: str | None = None


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
