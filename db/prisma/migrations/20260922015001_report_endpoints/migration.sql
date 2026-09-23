-- CreateTable
CREATE TABLE "sales_summary" (
    "id" SERIAL NOT NULL,
    "source_id" TEXT NOT NULL,
    "store_id" INTEGER NOT NULL,
    "business_date" DATE NOT NULL,
    "product_id" TEXT,
    "product_name" TEXT NOT NULL,
    "quantity" DECIMAL(16,3) NOT NULL,
    "gross_sales" DECIMAL(16,2) NOT NULL,
    "cost" DECIMAL(16,2),
    "tax" DECIMAL(16,2),
    "gross_profit" DECIMAL(16,2),
    "batch_id" TEXT,
    "loaded_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "sales_summary_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "product_movement" (
    "id" SERIAL NOT NULL,
    "source_id" TEXT NOT NULL,
    "store_id" INTEGER NOT NULL,
    "business_date" DATE NOT NULL,
    "product_name" TEXT NOT NULL,
    "product_category" TEXT,
    "sku_id" TEXT,
    "total_items_sold" DECIMAL(16,3) NOT NULL,
    "total_sales" DECIMAL(16,2) NOT NULL,
    "total_sales_returned" DECIMAL(16,2) NOT NULL DEFAULT 0,
    "total_discount" DECIMAL(16,2) NOT NULL DEFAULT 0,
    "discount_percent" DECIMAL(9,4),
    "item_net_sales" DECIMAL(16,2) NOT NULL,
    "average_cost" DECIMAL(16,2),
    "average_net_sales" DECIMAL(16,2),
    "gross_profit" DECIMAL(16,2),
    "gross_profit_percent" DECIMAL(9,4),
    "batch_id" TEXT,
    "loaded_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "product_movement_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "sales_summary_source_id_key" ON "sales_summary"("source_id");

-- CreateIndex
CREATE INDEX "sales_summary_business_date_store_id_idx" ON "sales_summary"("business_date", "store_id");

-- CreateIndex
CREATE INDEX "sales_summary_store_id_business_date_idx" ON "sales_summary"("store_id", "business_date");

-- CreateIndex
CREATE INDEX "sales_summary_product_name_idx" ON "sales_summary"("product_name");

-- CreateIndex
CREATE UNIQUE INDEX "product_movement_source_id_key" ON "product_movement"("source_id");

-- CreateIndex
CREATE INDEX "product_movement_business_date_store_id_idx" ON "product_movement"("business_date", "store_id");

-- CreateIndex
CREATE INDEX "product_movement_store_id_business_date_idx" ON "product_movement"("store_id", "business_date");

-- CreateIndex
CREATE INDEX "product_movement_product_category_idx" ON "product_movement"("product_category");

-- CreateIndex
CREATE INDEX "product_movement_sku_id_idx" ON "product_movement"("sku_id");

-- AddForeignKey
ALTER TABLE "sales_summary" ADD CONSTRAINT "sales_summary_store_id_fkey" FOREIGN KEY ("store_id") REFERENCES "store"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "product_movement" ADD CONSTRAINT "product_movement_store_id_fkey" FOREIGN KEY ("store_id") REFERENCES "store"("id") ON DELETE RESTRICT ON UPDATE CASCADE;
