import logging
import sys
import time
import traceback
from ChargingAutomation import run_charging_automation


logger = logging.getLogger(__name__)

ACTIVE_ITERATION_SECONDS = 5 * 60
IDLE_ITERATION_SECONDS = 30 * 60


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s: %(message)s',
        stream=sys.stdout
    )


def main():
    setup_logging()

    logger.info(
        'Charging Automation Started: active interval {}s, idle interval {}s'.format(
            ACTIVE_ITERATION_SECONDS, IDLE_ITERATION_SECONDS
        )
    )

    while True:
        try:
            is_actively_charging = run_charging_automation()

            if is_actively_charging:
                time.sleep(ACTIVE_ITERATION_SECONDS)
            else:
                time.sleep(IDLE_ITERATION_SECONDS)

        except Exception as e:
            logger.error('Automation crashed: {}'.format(e))
            logger.error(traceback.format_exc())
            logger.info('Sleeping for {} seconds...'.format(ACTIVE_ITERATION_SECONDS))
            time.sleep(ACTIVE_ITERATION_SECONDS)
        



if __name__ == '__main__':
    main()
