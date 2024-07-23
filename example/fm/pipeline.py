from sklearn.model_selection import train_test_split
from dask import dataframe as dd
from dask import delayed
from haversine import haversine
import pandas as pd
import numpy as np
import json
from rankfm.rankfm import RankFM
from rankfm.evaluation import hit_rate, reciprocal_rank, discounted_cumulative_gain, precision, recall
from aigear.pipeline import workflow, task
from aigear.manage.local import ModelManager


@delayed
def get_schema(purchase_history):
    schema = pd.json_normalize(
        purchase_history.head(1).to_dict(orient="records"),
        record_path='Items',
        meta=['_id', 'StoreId', 'RemoteUserId', 'ItemsCount']
    ).head(1)
    return schema


@task
def read_purchase_history(pur_history_path):
    purchase_history = dd.read_json(pur_history_path, lines=True, nrows=10000)  # nrows=10000

    purchase_history['Items'] = purchase_history['Items'].apply(
        lambda x: json.loads(x.replace("'", '"')),
        meta=('Items', 'object')
    )
    schema = get_schema(purchase_history).compute()

    purHist_df = purchase_history.map_partitions(
        lambda x: pd.json_normalize(
            x.to_dict(orient="records"),
            record_path='Items',
            meta=['_id', 'StoreId', 'RemoteUserId', 'ItemsCount']
        ),
        meta=schema
    )
    purHist_df = purHist_df.rename(columns={"_id": "ReceiptId", "TotalPrice": "SalesPriceWithTax", "Id": "ItemId"})
    purHist_df = purHist_df.dropna()

    # The number of sessions for the user must be greater than 1
    user_counts = purHist_df.groupby("RemoteUserId").RemoteUserId.size()
    purHist_df = purHist_df[purHist_df['RemoteUserId'].isin(user_counts[user_counts > 1].index)]
    purHist_df = purHist_df.reset_index(drop=True)

    # Integer type has smaller memory and higher efficiency
    purHist_df["SalesPriceWithTax"] = purHist_df['SalesPriceWithTax'].astype('float64')
    purHist_df['StoreId'] = purHist_df['StoreId'].astype('int8')
    purHist_df['RemoteUserId'] = purHist_df['RemoteUserId'].astype('int64')
    purHist_df['ItemId'] = purHist_df['ItemId'].astype('int64')
    purHist_df['ReceiptId'] = purHist_df['ReceiptId'].astype('str')
    purHist_df['ItemsCount'] = purHist_df['ItemsCount'].astype('int64')
    return purHist_df


def extract_category(df):
    bumon_list = []
    minibumon_list = []
    bumon_id_list = []
    for family_tree_json in df['FamilyTree']:
        for family_tree in family_tree_json:
            category_type = family_tree.get('CategoryType')
            remote_category_id = family_tree.get('RemoteCategoryId', '')
            if category_type == "BUMON_CODE":
                bumon_list.append(remote_category_id)
                category_id = family_tree.get('Id', '')
                bumon_id_list.append(category_id)
            elif category_type == "MINI_BUMON_CODE":
                minibumon_list.append(remote_category_id)

    df["BUMON_ID"] = bumon_id_list
    df["code"] = [f"{b}-{m}" for b, m in zip(bumon_list, minibumon_list)]

    return df[["ItemId", "BUMON_ID", "code"]]


@task
def read_item_master(item_master_path):
    item_masters = dd.read_json(item_master_path, lines=True)

    item_masters['FamilyTree'] = item_masters['FamilyTree'].apply(
        lambda x: eval(x),
        meta=('FamilyTree', 'object')
    )
    item_masters_df = item_masters.map_partitions(
        extract_category,
        meta={'ItemId': 'str', 'BUMON_ID': 'str', 'code': 'str'}
    )
    item_masters_df['ItemId'] = item_masters_df['ItemId'].astype('int64')
    return item_masters_df


@task
def read_user_attrib(user_attrib_path):
    user_attrib = dd.read_json(user_attrib_path, lines=True)

    user_attrib['Location'] = user_attrib['Location'].apply(
        lambda json_str: tuple(eval(json_str).get('coordinates')[::-1]),
        meta=('coords_user', 'object')
    )
    user_attrib = user_attrib.rename(columns={"Location": "coords_user"})
    user_attrib = user_attrib.dropna()

    user_attrib['AgeRange'] = user_attrib['AgeRange'].astype('int8')
    user_attrib['RemoteUserId'] = user_attrib['RemoteUserId'].astype('int64')
    return user_attrib


def extract_coords(store_list):
    store_list['coords_store'] = list(zip(store_list['location.lat'], store_list['location.long']))
    store_list = store_list.rename(columns={"id": "StoreId"})
    return store_list[["StoreId", 'coords_store']]


@task
def read_store_list(store_list_path):
    store_list = dd.read_csv(store_list_path, usecols=['id', 'location.lat', 'location.long'])

    store_list = store_list.map_partitions(
        extract_coords,
        meta={'StoreId': 'int8', 'coords_store': 'object'}
    )
    return store_list


def extract_line_code(df):
    line_code_list = []
    for family_tree_json in df['FamilyTree']:
        for family_tree in family_tree_json:
            category_type = family_tree.get('CategoryType')
            remote_category_id = family_tree.get('RemoteCategoryId')
            if category_type == "LINE_CODE":
                line_code_list.append(remote_category_id)

    df["LINE_CODE"] = line_code_list
    df = df.rename(columns={"_id": "BUMON_ID"})
    return df[["BUMON_ID", "LINE_CODE"]]


@task
def read_item_categories(item_categories_path):
    item_categories = dd.read_json(item_categories_path, lines=True)
    item_categories['FamilyTree'] = item_categories['FamilyTree'].apply(
        lambda x: eval(x),
        meta=('FamilyTree', 'object')
    )

    item_categories = item_categories.map_partitions(
        extract_line_code,
        meta={'BUMON_ID': 'str', 'LINE_CODE': 'int8'}
    )
    return item_categories


@task
def compute_distance(pur_history, user_attrib, store_list):
    pur_history_merged = pur_history.merge(user_attrib, on="RemoteUserId")
    pur_history_merged = pur_history_merged.merge(store_list, on="StoreId")
    pur_history_merged = pur_history_merged.reset_index(drop=True)

    pur_history_merged['distance'] = pur_history_merged[['coords_user', 'coords_store']].map_partitions(
        lambda df: df.apply(lambda row: round(haversine(row['coords_user'], row['coords_store']), 2), axis=1),
        meta=('distance', 'float64')
    )
    pur_history_merged = pur_history_merged.drop(columns=['coords_user', 'coords_store'])
    return pur_history_merged


@task
def add_categories(pur_history_merged, item_master, item_categories):
    item_master = item_master.merge(item_categories, on="BUMON_ID")
    pur_history_merged = pur_history_merged.merge(item_master, on="ItemId")

    # The number of code must be greater than 10
    pur_history_merged = pur_history_merged.drop_duplicates(subset=["RemoteUserId", "code"], keep="last")
    code_counts = pur_history_merged.groupby("code").code.size()
    pur_history_merged = pur_history_merged[pur_history_merged["code"].isin(code_counts[code_counts > 10].index)]

    pur_history_merged = pur_history_merged.drop(columns=['BUMON_ID', "ItemId"])
    pur_history_merged = pur_history_merged.reset_index(drop=True)
    pur_history_merged['code'] = pur_history_merged['code'].astype('str')
    return pur_history_merged


@task
def split_data(pur_history_merged):
    def split_partition(df):
        train, test = train_test_split(df, train_size=0.8)
        train['split'] = 'train'
        test['split'] = 'test'
        return pd.concat([train, test])

    data_splited = pur_history_merged.map_partitions(
        split_partition,
        meta={
            'SalesPriceWithTax': 'float64',
            'ReceiptId': object,
            'StoreId': 'int8',
            'RemoteUserId': 'int64',
            'ItemsCount': 'int64',
            'Gender': 'float64',
            'AgeRange': 'float64',
            'distance': 'float64',
            'code': object,
            'LINE_CODE': 'float64',
            'split': object,
        }
    ).persist()
    train_data = data_splited[data_splited['split'] == 'train'].drop(columns=['split']).reset_index(drop=True)
    valid_data = data_splited[data_splited['split'] == 'test'].drop(columns=['split']).reset_index(drop=True)

    return train_data, valid_data


def _get_user_feature(idpos):
    base_df = idpos[['RemoteUserId', 'AgeRange', 'Gender', 'distance']].copy().drop_duplicates(subset='RemoteUserId',
                                                                                               keep='first').reset_index(
        drop=True)

    base_df['distance'] = base_df['distance'].apply(
        lambda x: np.log(x) if x != 0 else 0,
        meta=('distance', 'float64')
    )

    avg_purchased_item_df = idpos[['RemoteUserId', 'ItemsCount', 'ReceiptId']].copy().drop_duplicates().groupby(
        ['RemoteUserId', 'ReceiptId'],
        observed=True
    ).agg('mean').reset_index(drop=False)[['RemoteUserId', 'ItemsCount']].rename(
        columns={'ItemsCount': 'avg_purchased_item'})

    joined_df = base_df.merge(avg_purchased_item_df, on='RemoteUserId', how='left')

    avg_unit_price_df = idpos[['RemoteUserId', 'SalesPriceWithTax', 'ReceiptId']].copy().groupby(
        ['RemoteUserId', 'ReceiptId'],
        observed=True
    ).agg('mean').reset_index(drop=False).rename(columns={'SalesPriceWithTax': 'avg_unit_price'})[
        ['RemoteUserId', 'avg_unit_price']]
    joined_df = joined_df.merge(avg_unit_price_df, on='RemoteUserId', how='left')

    def cut_data(joined_df):
        joined_df['distance'] = pd.cut(joined_df['distance'], bins=[0, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 16])
        joined_df['avg_purchased_item'] = pd.cut(joined_df['avg_purchased_item'],
                                                 bins=[0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 25, 30])
        joined_df['avg_unit_price'] = pd.cut(joined_df['avg_unit_price'],
                                             bins=[0, 200, 400, 600, 800, 1000, 1500, 2000])
        return joined_df

    joined_df = joined_df.map_partitions(cut_data)

    columns_list = ['AgeRange', 'Gender', 'distance', 'avg_purchased_item', 'avg_unit_price']
    joined_df = joined_df.categorize(columns=columns_list)
    user_features = dd.get_dummies(joined_df, columns=columns_list, sparse=True)
    user_features = user_features.drop_duplicates(subset='RemoteUserId', keep='first')

    return user_features


def _get_item_feature(idpos):
    item_idpos = idpos.copy()

    item_idpos['SalesPriceWithTax'] = item_idpos['SalesPriceWithTax'].apply(
        lambda x: np.log(x) if x != 0 else 0,
        meta=('distance', 'float64')
    )

    def cut_data(item_idpos):
        item_idpos['SalesPriceWithTax'] = pd.cut(item_idpos['SalesPriceWithTax'], bins=[0, 2, 4, 5, 6, 7, 8])
        return item_idpos

    item_idpos = item_idpos.map_partitions(cut_data)

    columns_list = ['LINE_CODE', 'SalesPriceWithTax']
    item_idpos = item_idpos.categorize(columns=columns_list)
    item_features = dd.get_dummies(item_idpos, columns=columns_list, sparse=True)
    item_features = item_features.drop_duplicates(subset='code', keep='first')

    return item_features


@task
def get_train_features(train_data):
    user_data = train_data[
        ['RemoteUserId', 'AgeRange', 'Gender', 'distance', 'SalesPriceWithTax', 'ReceiptId', 'ItemsCount']]
    user_feature_set = _get_user_feature(user_data)

    item_data = train_data[['code', 'LINE_CODE', 'SalesPriceWithTax']]
    item_feature_set = _get_item_feature(item_data)

    interactions = train_data[['RemoteUserId', 'code']].compute()
    user_features = user_feature_set.compute()
    item_features = item_feature_set.compute()
    return interactions, user_features, item_features


@task
def train_model(interactions, user_features, item_features):
    model = RankFM(
        factors=64,
        loss="bpr",
        max_samples=20,
        alpha=0.01,
        sigma=0.1,
        learning_rate=0.1,
        learning_schedule="invscaling",
    )

    model.fit(
        interactions,
        user_features=user_features,
        item_features=item_features,
        epochs=35,
        verbose=True
    )
    return model


@task
def model_evaluation(model, valid_data, recnum):
    validData_df = valid_data.compute()
    interactions_valid = validData_df[['RemoteUserId', 'code']]
    valid_hit_rate = hit_rate(model, interactions_valid, k=recnum)
    valid_reciprocal_rank = reciprocal_rank(model, interactions_valid, k=recnum)
    valid_dcg = discounted_cumulative_gain(model, interactions_valid, k=recnum)
    valid_precision = precision(model, interactions_valid, k=recnum)
    valid_recall = recall(model, interactions_valid, k=recnum)
    message_content = (f"Hit rate: {valid_hit_rate}\n" +
                       f"Reciprocal rank: {valid_reciprocal_rank}\n" +
                       f"Discounted cumulative gain: {valid_dcg}\n" +
                       f"Precision: {valid_precision}\n" +
                       f"Recall: {valid_recall}")
    print(message_content)


@task
def manage_model(model, model_name):
    model_manager = ModelManager()
    model_manager.pickle_save(model, model_name)


@workflow
def fm_pipeline():
    pur_history_path = r'D:\git_work\Taiyo_pipeline_code\data\PurchaseHistoryForCompany.zip'
    item_master_path = r'D:\git_work\Taiyo_pipeline_code\data\ItemMasters.zip'
    user_attrib_path = r'D:\git_work\Taiyo_pipeline_code\data\UserAttributes.zip'
    store_list_path = r'D:\git_work\Taiyo_pipeline_code\data\storeList.csv'
    item_categories_path = r'D:\git_work\Taiyo_pipeline_code\data\ItemCategories.zip'
    model_name = "FM"
    recnum = 9

    pur_history = read_purchase_history(pur_history_path)
    user_attrib = read_user_attrib(user_attrib_path)
    store_list = read_store_list(store_list_path)
    item_master = read_item_master(item_master_path)
    item_categories = read_item_categories(item_categories_path)

    pur_history_distance = compute_distance(pur_history, user_attrib, store_list)
    pur_history_dataset = add_categories(pur_history_distance, item_master, item_categories)
    train_data, valid_data = split_data(pur_history_dataset)
    interactions, user_features, item_features = get_train_features(train_data)
    model = train_model(interactions, user_features, item_features)

    model_evaluation(model, valid_data, recnum)

    manage_model(model, model_name)


if __name__ == '__main__':
    # import os
    # my_pipeline()
    fm_pipeline.run_in_executor()

    # mem_limit_train = "128m"
    # mem_limit_service = "500m"
    # cpu_count_train = 1
    # cpu_count_service = 2
    #
    # current_directory = os.getcwd()
    # volumes = {
    #     current_directory: {'bind': "/pipeline", 'mode': 'rw'}
    # }
    # hostname = "demo-host"
    # ports = {'50051/tcp': 50051}
    # service_dir = "demo"
    #
    # fm_pipeline.deploy(
    #     volumes=volumes,
    #     skip_build_image=True,
    #     cpu_count=cpu_count_train,
    #     mem_limit=mem_limit_train
    # ).to_service(
    #     hostname=hostname,
    #     ports=ports,
    #     volumes=volumes,
    #     tag=service_dir,
    #     cpu_count=cpu_count_service,
    #     mem_limit=mem_limit_service
    # )
