
# --------------------------------------------------------------------------- #
# Skills (Addons)
# --------------------------------------------------------------------------- #
@router.get("/skills")
def list_skills_rest(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = db.query(Procedure).filter(Procedure.company_id == user.company_id)
    return {"skills": [
        {
            "name": p.slug,
            "description": p.intent,
            "autonomy_level": p.autonomy_level.value if hasattr(p.autonomy_level, "value") else p.autonomy_level,
            "success_rate": p.success_rate,
            "execution_count": p.execution_count,
        } for p in q.all()
    ]}


@router.get("/skills/{name}")
def get_skill_rest(
    name: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    p = db.query(Procedure).filter(
        Procedure.company_id == user.company_id, Procedure.slug == name
    ).first()
    if not p:
        raise HTTPException(status_code=404, detail="Skill not found")
    return {
        "name": p.slug,
        "description": p.intent,
        "autonomy_level": p.autonomy_level.value if hasattr(p.autonomy_level, "value") else p.autonomy_level,
        "success_rate": p.success_rate,
        "execution_count": p.execution_count,
        "steps": [s.instruction for s in sorted(p.steps, key=lambda x: x.step_order)],
        "rules": [r.content for r in p.rules]
    }
