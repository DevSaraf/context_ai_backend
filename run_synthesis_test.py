import asyncio
import logging
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

from sqlalchemy import text
from app.database import SessionLocal
from app.procedure_models import SynthesisRun
from app.synthesis import run_synthesis

async def main():
    db = SessionLocal()
    try:
        users = db.execute(text("SELECT id, company_id FROM users WHERE company_id = 'KRAB' LIMIT 1")).fetchall()
        if not users:
            print('No users found')
            return
        u_id, c_id = users[0]
        
        run = SynthesisRun(company_id=c_id, user_id=u_id)
        db.add(run)
        db.commit()
        db.refresh(run)

        print(f'Running synthesis for user {u_id}, company {c_id}, run_id {run.id} with force=True')
        
        # Use domain='support' to limit to a few clusters for testing
        await run_synthesis(db, company_id=c_id, user_id=u_id, domain=None, force=True, min_cluster_size=2, run_id=run.id)
        
        print('--- Results ---')
        count = db.execute(text("SELECT count(*) FROM procedures WHERE company_id = :c_id"), {'c_id': c_id}).scalar()
        print(f'Total procedures in DB for KRAB: {count}')
        
        run_info = db.execute(text("SELECT chunks_considered, clusters_found, procedures_created, procedures_updated, procedures_unchanged, error, status FROM synthesis_runs WHERE id = :run_id"), {'run_id': run.id}).fetchall()
        print('\nRun Info:', run_info)
    finally:
        db.close()

asyncio.run(main())
