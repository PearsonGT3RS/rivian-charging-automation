import logging
import sys
import time
import traceback # <--- NEW: Import the traceback library
from ChargingAutomation import run_charging_automation


logger = logging.getLogger(__name__)


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s: %(message)s',
        stream=sys.stdout
    )


def main():
    setup_logging()

    iteration_time = 5 * 60

    logger.info('Charging Automation Started: run every {} seconds'.format(iteration_time))

    while True:
        try:
            is_actively_charging = run_charging_automation()
            
            if is_actively_charging:
                time.sleep(iteration_time)  
            else:
                time.sleep(1800) 
                
        except Exception as e:
            # --- NEW: Print the full stack trace to the logs ---
            logger.error(f"Automation crashed: {e}")
            logger.error(traceback.format_exc())
            logger.info('Sleeping for {} seconds...'.format(iteration_time))
            time.sleep(iteration_time)
            run_charging_automation()
        



if __name__ == '__main__':
    main()
