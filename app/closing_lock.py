def lock_day(db,day):
    if db.bind.dialect.name=='postgresql':
        from sqlalchemy import text
        db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':75000000+day.toordinal()})
