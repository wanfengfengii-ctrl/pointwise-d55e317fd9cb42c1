def publish_result(store, results, receipts, plan_id, payload):
    if results.store is not store or receipts.store is not store:
        raise ValueError("publication requires one archive connection")
    with store.transaction():
        prior = receipts.find(plan_id)
        if prior is not None:
            record = results.read(prior.result_id)
            if record.payload.value["binding"]["plan_id"] != plan_id:
                raise ValueError("damaged completion binding")
            return prior.result_id
        result_id = results.stage(plan_id, payload)
        store.checkpoint("publication_after_result")
        receipts.record_completion(plan_id, result_id)
        store.checkpoint("publication_after_receipt")
        return result_id
