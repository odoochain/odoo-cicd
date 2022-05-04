CREATE OR REPLACE FUNCTION func_trigger_queuejob_state_check_at_commit()
RETURNS trigger AS
$BODY$
DECLARE qj record;
DECLARE counted int;

BEGIN
    SELECT id, state, identity_key
	INTO qj
	FROM stock_move
	WHERE id = NEW.id;

	IF qj.state = 'started' THEN
		IF coalesce(qj.identity_key, '') <> '' THEN
			SELECT count(*)
			INTO counted
			FROM queue_job
			WHERE identity_key = qj.identity_key
			AND state = 'started';

			IF counted > 1 THEN
				RAISE EXCEPTION 'Cannot start two jobs for same identity key';
			END IF;

		END IF;

	END IF;

    RETURN NEW;
END
$BODY$
    LANGUAGE 'plpgsql' SECURITY INVOKER
;

DROP TRIGGER IF EXISTS trigger_queuejob_state_check_at_commit ON queue_job;

CREATE CONSTRAINT TRIGGER trigger_stock_move_checks_update_at_commit
AFTER UPDATE ON queue_job DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE PROCEDURE func_trigger_queuejob_state_check_at_commit();