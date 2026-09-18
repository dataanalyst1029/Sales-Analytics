-- CreateEnum
CREATE TYPE "Estate" AS ENUM ('STOREHUB', 'ACCOUNTING');

-- CreateTable
CREATE TABLE "store" (
    "id" SERIAL NOT NULL,
    "source_id" TEXT NOT NULL,
    "name" TEXT NOT NULL,
    "code" TEXT,
    "estate" "Estate" NOT NULL,
    "slug" TEXT NOT NULL,
    "recon_name" TEXT,

    CONSTRAINT "store_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "product" (
    "id" SERIAL NOT NULL,
    "source_id" TEXT NOT NULL,
    "name" TEXT NOT NULL,
    "estate" "Estate" NOT NULL,

    CONSTRAINT "product_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "transaction" (
    "id" SERIAL NOT NULL,
    "source_id" TEXT NOT NULL,
    "store_id" INTEGER NOT NULL,
    "estate" "Estate" NOT NULL,
    "ref_number" TEXT,
    "business_date" DATE NOT NULL,
    "transacted_at" TIMESTAMPTZ(3),
    "has_time" BOOLEAN NOT NULL DEFAULT false,
    "local_hour" INTEGER,
    "amount" DECIMAL(16,2) NOT NULL,
    "quantity" INTEGER NOT NULL DEFAULT 1,
    "payment_method" TEXT,
    "cashier_name" TEXT,
    "status" TEXT,
    "is_deleted" BOOLEAN NOT NULL DEFAULT false,
    "is_flagged" BOOLEAN NOT NULL DEFAULT false,
    "flag_reason" TEXT,
    "channel" TEXT,
    "register_id" TEXT,
    "terminal_number" TEXT,
    "invoice_number" TEXT,
    "employee_id" TEXT,
    "sub_total" DECIMAL(16,2),
    "tax_amount" DECIMAL(16,2),
    "discount_amount" DECIMAL(16,2),
    "service_charge" DECIMAL(16,2),
    "rounded_amount" DECIMAL(16,2),
    "batch_id" TEXT,
    "batch_name" TEXT,
    "source_created_at" TIMESTAMPTZ(3),
    "loaded_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "transaction_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "transaction_item" (
    "id" SERIAL NOT NULL,
    "transaction_id" INTEGER NOT NULL,
    "product_source_id" TEXT NOT NULL,
    "product_id" INTEGER,
    "product_name" TEXT,
    "quantity" DECIMAL(12,3) NOT NULL,
    "unit_price" DECIMAL(16,2) NOT NULL,
    "sub_total" DECIMAL(16,2),
    "tax_amount" DECIMAL(16,2),
    "discount" DECIMAL(16,2) NOT NULL DEFAULT 0,
    "line_total" DECIMAL(16,2) NOT NULL,
    "item_type" TEXT,

    CONSTRAINT "transaction_item_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "payment" (
    "id" SERIAL NOT NULL,
    "transaction_id" INTEGER NOT NULL,
    "method" TEXT NOT NULL,
    "amount" DECIMAL(16,2) NOT NULL,

    CONSTRAINT "payment_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "receipt" (
    "id" SERIAL NOT NULL,
    "source_id" TEXT NOT NULL,
    "store_id" INTEGER NOT NULL,
    "receipt_number" TEXT,
    "business_date" DATE NOT NULL,
    "time_raw" TEXT,
    "local_hour" INTEGER,
    "terminal_number" TEXT,
    "cashier" TEXT,
    "serviced_by" TEXT,
    "customer_name" TEXT,
    "posted" BOOLEAN NOT NULL DEFAULT false,
    "total_gross" DECIMAL(16,2) NOT NULL,
    "discount" DECIMAL(16,2) NOT NULL DEFAULT 0,
    "void_amount" DECIMAL(16,2) NOT NULL DEFAULT 0,
    "service" DECIMAL(16,2) NOT NULL DEFAULT 0,
    "senior" DECIMAL(16,2) NOT NULL DEFAULT 0,
    "pwd" DECIMAL(16,2) NOT NULL DEFAULT 0,
    "nac" DECIMAL(16,2) NOT NULL DEFAULT 0,
    "solo_parent" DECIMAL(16,2) NOT NULL DEFAULT 0,
    "mov" DECIMAL(16,2) NOT NULL DEFAULT 0,
    "diplomat" DECIMAL(16,2) NOT NULL DEFAULT 0,
    "evat" DECIMAL(16,2) NOT NULL DEFAULT 0,
    "tax" DECIMAL(16,2) NOT NULL DEFAULT 0,
    "local_tax" DECIMAL(16,2) NOT NULL DEFAULT 0,
    "amusement_tax" DECIMAL(16,2) NOT NULL DEFAULT 0,
    "ewt" DECIMAL(16,2) NOT NULL DEFAULT 0,
    "feedback_rating" INTEGER,
    "batch_id" TEXT,
    "source_created_at" TIMESTAMPTZ(3),
    "loaded_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "receipt_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "load_run" (
    "id" SERIAL NOT NULL,
    "dataset" TEXT NOT NULL,
    "from_date" DATE NOT NULL,
    "to_date" DATE NOT NULL,
    "started_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "finished_at" TIMESTAMP(3),
    "ok" BOOLEAN NOT NULL DEFAULT false,
    "rows_fetched" INTEGER NOT NULL DEFAULT 0,
    "rows_written" INTEGER NOT NULL DEFAULT 0,
    "api_total" INTEGER,
    "error" TEXT,

    CONSTRAINT "load_run_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "store_source_id_key" ON "store"("source_id");

-- CreateIndex
CREATE INDEX "store_estate_idx" ON "store"("estate");

-- CreateIndex
CREATE UNIQUE INDEX "product_source_id_key" ON "product"("source_id");

-- CreateIndex
CREATE UNIQUE INDEX "transaction_source_id_key" ON "transaction"("source_id");

-- CreateIndex
CREATE INDEX "transaction_business_date_store_id_idx" ON "transaction"("business_date", "store_id");

-- CreateIndex
CREATE INDEX "transaction_store_id_business_date_idx" ON "transaction"("store_id", "business_date");

-- CreateIndex
CREATE INDEX "transaction_estate_business_date_idx" ON "transaction"("estate", "business_date");

-- CreateIndex
CREATE INDEX "transaction_local_hour_idx" ON "transaction"("local_hour");

-- CreateIndex
CREATE INDEX "transaction_is_flagged_is_deleted_idx" ON "transaction"("is_flagged", "is_deleted");

-- CreateIndex
CREATE INDEX "transaction_item_transaction_id_idx" ON "transaction_item"("transaction_id");

-- CreateIndex
CREATE INDEX "transaction_item_product_source_id_idx" ON "transaction_item"("product_source_id");

-- CreateIndex
CREATE INDEX "payment_transaction_id_idx" ON "payment"("transaction_id");

-- CreateIndex
CREATE UNIQUE INDEX "receipt_source_id_key" ON "receipt"("source_id");

-- CreateIndex
CREATE INDEX "receipt_business_date_store_id_idx" ON "receipt"("business_date", "store_id");

-- CreateIndex
CREATE INDEX "receipt_store_id_business_date_idx" ON "receipt"("store_id", "business_date");

-- CreateIndex
CREATE INDEX "receipt_local_hour_idx" ON "receipt"("local_hour");

-- CreateIndex
CREATE INDEX "load_run_dataset_from_date_idx" ON "load_run"("dataset", "from_date");

-- AddForeignKey
ALTER TABLE "transaction" ADD CONSTRAINT "transaction_store_id_fkey" FOREIGN KEY ("store_id") REFERENCES "store"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "transaction_item" ADD CONSTRAINT "transaction_item_transaction_id_fkey" FOREIGN KEY ("transaction_id") REFERENCES "transaction"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "transaction_item" ADD CONSTRAINT "transaction_item_product_id_fkey" FOREIGN KEY ("product_id") REFERENCES "product"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "payment" ADD CONSTRAINT "payment_transaction_id_fkey" FOREIGN KEY ("transaction_id") REFERENCES "transaction"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "receipt" ADD CONSTRAINT "receipt_store_id_fkey" FOREIGN KEY ("store_id") REFERENCES "store"("id") ON DELETE RESTRICT ON UPDATE CASCADE;
