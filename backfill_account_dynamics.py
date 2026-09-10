import sys

from service.cleanup_service.backfill_account_dynamics import AccountDynamicBackfill
from utils import globals


if __name__ == '__main__':
    account_key = sys.argv[1] if len(sys.argv) > 1 else globals.my_user_id
    AccountDynamicBackfill(account_key).backfill_status_one()
